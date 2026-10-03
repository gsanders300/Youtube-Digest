"""YouTube Data API helpers shared by every script: retrying requests and channel resolution.

`execute` wraps a built googleapiclient request with retry/backoff, and
`resolve_channel` turns what channels.yml knows about a channel (its @handle,
stored channel ID, and title) into a verified channel ID and uploads playlist.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import googleapiclient.errors

YOUTUBE_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
YOUTUBE_API_MAX_RETRIES = 3
YOUTUBE_API_RETRY_BASE_DELAY_SECONDS = 2


@dataclass(frozen=True)
class ResolvedChannel:
    """A channel whose uploads playlist has been verified to be queryable."""

    id: str
    handle: str | None
    title: str | None
    uploads_playlist_id: str
    strategy: str  # "handle", "id", or "search": the step that resolved it.


def execute(request: Any) -> dict[str, Any]:
    """Executes a YouTube Data API request with retry/backoff.

    Quota-exceeded errors are not retried, since the daily quota will not
    recover within a single run. Rate-limit (429) and server (5xx) errors
    are retried with exponential backoff, since those are often transient.

    Args:
        request: A built (not yet executed) googleapiclient request object.

    Returns:
        The parsed JSON response body.

    Raises:
        googleapiclient.errors.HttpError: If the request fails with a
            non-retryable error, or all retries are exhausted.
    """
    attempt = 0
    while True:
        try:
            return request.execute()
        except googleapiclient.errors.HttpError as e:
            status = e.resp.status if e.resp is not None else None

            if status == 403 and "quotaexceeded" in str(e).lower():
                print("YouTube API quota exceeded; not retrying (quota resets daily).")
                raise

            if (
                status not in YOUTUBE_RETRYABLE_STATUS_CODES
                or attempt >= YOUTUBE_API_MAX_RETRIES
            ):
                raise

            delay = YOUTUBE_API_RETRY_BASE_DELAY_SECONDS * (2**attempt)
            print(
                f"YouTube API error (status {status}); retrying in {delay}s "
                f"(attempt {attempt + 1}/{YOUTUBE_API_MAX_RETRIES})..."
            )
            time.sleep(delay)
            attempt += 1


def is_playlist_not_found(error: Exception) -> bool:
    """Returns True for a YouTube 'playlist not found' (404) error.

    Args:
        error: The exception raised by a playlistItems request.

    Returns:
        True if the error is an HttpError with HTTP status 404 and a
        playlistNotFound reason, False otherwise.
    """
    if not isinstance(error, googleapiclient.errors.HttpError):
        return False
    status = error.resp.status if error.resp is not None else None
    return status == 404 and "playlistnotfound" in str(error).lower()


def normalize_handle(custom_url: str | None) -> str | None:
    """Returns a channel's snippet.customUrl as an @handle, or None if it has none.

    Args:
        custom_url: The customUrl from a channels().list snippet, with or without "@".

    Returns:
        The handle with a leading "@", or None if custom_url is empty.
    """
    if not custom_url:
        return None
    return custom_url if custom_url.startswith("@") else "@" + custom_url


def _uploads_playlist_works(youtube: Any, uploads_playlist_id: str) -> bool:
    """Returns True if an uploads playlist can actually be queried.

    A channel can resolve successfully yet expose an uploads playlist that
    playlistItems reports as not found (for example an empty channel, or a
    wrong channel matched by a bad handle).

    Raises:
        googleapiclient.errors.HttpError: For errors other than playlistNotFound.
    """
    try:
        execute(
            youtube.playlistItems().list(
                part="id", playlistId=uploads_playlist_id, maxResults=1
            )
        )
        return True
    except googleapiclient.errors.HttpError as error:
        if is_playlist_not_found(error):
            return False
        raise


def _lookup(youtube: Any, selector: dict[str, str]) -> dict[str, Any] | None:
    """Returns the first channels().list item for a forHandle/id selector, or None."""
    response = execute(
        youtube.channels().list(part="snippet,contentDetails", **selector)
    )
    items = response.get("items")
    return items[0] if items else None


def _search_by_title(youtube: Any, title: str) -> str | None:
    """Returns the channel ID of the top channel search hit for a title, or None."""
    response = execute(
        youtube.search().list(part="snippet", q=title, type="channel", maxResults=1)
    )
    items = response.get("items")
    return items[0].get("id", {}).get("channelId") if items else None


def resolve_channel(
    youtube: Any,
    *,
    handle: str | None = None,
    channel_id: str | None = None,
    title: str | None = None,
) -> ResolvedChannel | None:
    """Resolves a channel to a verified channel ID and uploads playlist.

    Tries, in order: the @handle, the channel ID, then a channel search by
    title. Every candidate's uploads playlist is probed before it is accepted,
    so a lookup that returns an empty or wrong channel is rejected. The title
    search runs only when a title is given; it costs 100 quota units and can
    match the wrong channel, so its hit must also have exactly that title
    (ignoring case).

    Args:
        youtube: A googleapiclient YouTube Data API v3 client.
        handle: The channel's @handle, if known. Ignored unless it starts with "@".
        channel_id: The channel's UC... ID, if known.
        title: The channel's display title. Pass it only to allow the search step.

    Returns:
        The resolved channel, or None if no step produced a working uploads playlist.

    Raises:
        googleapiclient.errors.HttpError: If an API request fails.
    """
    candidates: list[tuple[str, dict[str, str]]] = []
    if handle and handle.startswith("@"):
        candidates.append(("handle", {"forHandle": handle}))
    if channel_id:
        candidates.append(("id", {"id": channel_id}))

    for strategy, selector in candidates:
        resolved = _accept(youtube, _lookup(youtube, selector), strategy, handle)
        if resolved:
            return resolved

    if title:
        found_id = _search_by_title(youtube, title)
        item = _lookup(youtube, {"id": found_id}) if found_id else None
        found_title = (item or {}).get("snippet", {}).get("title") or ""
        if found_title.casefold() == title.casefold():
            return _accept(youtube, item, "search", handle)

    return None


def _accept(
    youtube: Any, item: dict[str, Any] | None, strategy: str, handle: str | None
) -> ResolvedChannel | None:
    """Builds a ResolvedChannel from a channels().list item if its uploads playlist works."""
    if not item or not item.get("id"):
        return None
    uploads = item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    if not uploads or not _uploads_playlist_works(youtube, uploads):
        return None

    snippet = item.get("snippet", {})
    # Prefer the API's handle casing; fall back to the handle that resolved it.
    resolved_handle = normalize_handle(snippet.get("customUrl"))
    if not resolved_handle and strategy == "handle":
        resolved_handle = handle
    return ResolvedChannel(
        id=item["id"],
        handle=resolved_handle,
        title=snippet.get("title"),
        uploads_playlist_id=uploads,
        strategy=strategy,
    )
