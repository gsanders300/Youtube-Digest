"""Tests for youtube_api: request retry/backoff and channel resolution."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import googleapiclient.errors
import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import youtube_api
from youtube_api import (
    YOUTUBE_API_MAX_RETRIES,
    execute,
    is_playlist_not_found,
    normalize_handle,
    resolve_channel,
)

CHANNEL_ID = "UCOXRjenlq9PmlTqd_JhAbMQ"
UPLOADS_ID = "UUOXRjenlq9PmlTqd_JhAbMQ"


def _http_error(
    status: int, message: bytes = b"error"
) -> googleapiclient.errors.HttpError:
    """Builds a fake googleapiclient HttpError with a given status code."""
    resp = MagicMock()
    resp.status = status
    return googleapiclient.errors.HttpError(resp, message, uri="https://example.com")


def _channel(
    channel_id: str = CHANNEL_ID,
    title: str = "Eric Tech",
    custom_url: str | None = "@ericwtech",
    uploads: str | None = UPLOADS_ID,
) -> dict[str, Any]:
    """Builds a fake channels().list response body with one item."""
    snippet: dict[str, Any] = {"title": title}
    if custom_url is not None:
        snippet["customUrl"] = custom_url
    related: dict[str, Any] = {}
    if uploads is not None:
        related["uploads"] = uploads
    return {
        "items": [
            {
                "id": channel_id,
                "snippet": snippet,
                "contentDetails": {"relatedPlaylists": related},
            }
        ]
    }


NO_ITEMS: dict[str, Any] = {"items": []}


def _youtube(*channel_responses: dict[str, Any]) -> MagicMock:
    """Builds a mocked YouTube client whose channels().list calls return responses in order."""
    youtube = MagicMock()
    youtube.channels.return_value.list.return_value.execute.side_effect = list(
        channel_responses
    )
    return youtube


def _list_kwargs(youtube: MagicMock) -> list[dict[str, Any]]:
    """Returns the keyword arguments of every channels().list call."""
    return [c.kwargs for c in youtube.channels.return_value.list.call_args_list]


class TestExecute:
    """Tests for the retry/backoff wrapper."""

    def test_succeeds_on_first_try(self) -> None:
        """A successful call should not be retried."""
        request = MagicMock()
        request.execute.return_value = {"items": []}

        assert execute(request) == {"items": []}
        assert request.execute.call_count == 1

    def test_retries_transient_error_then_succeeds(self) -> None:
        """A 503 should be retried once and then succeed."""
        request = MagicMock()
        request.execute.side_effect = [_http_error(503), {"items": ["ok"]}]

        with patch("time.sleep"):
            assert execute(request) == {"items": ["ok"]}
        assert request.execute.call_count == 2

    def test_quota_exceeded_is_not_retried(self) -> None:
        """A quotaExceeded 403 should fail immediately without retrying."""
        request = MagicMock()
        request.execute.side_effect = _http_error(403, b"quotaExceeded: daily limit")

        with patch("time.sleep") as mock_sleep:
            with pytest.raises(googleapiclient.errors.HttpError):
                execute(request)

        assert request.execute.call_count == 1
        mock_sleep.assert_not_called()

    def test_non_retryable_status_raises_immediately(self) -> None:
        """A non-retryable status (e.g. 404) should not be retried."""
        request = MagicMock()
        request.execute.side_effect = _http_error(404, b"not found")

        with pytest.raises(googleapiclient.errors.HttpError):
            execute(request)
        assert request.execute.call_count == 1

    def test_exhausts_retries_and_raises(self) -> None:
        """Persistent 503s should be retried up to the max and then raise."""
        request = MagicMock()
        request.execute.side_effect = _http_error(503)

        with patch("time.sleep"):
            with pytest.raises(googleapiclient.errors.HttpError):
                execute(request)
        assert request.execute.call_count == YOUTUBE_API_MAX_RETRIES + 1


class TestIsPlaylistNotFound:
    """Tests for playlistNotFound classification."""

    def test_404_playlist_not_found(self) -> None:
        """A 404 with the playlistNotFound reason is recognised."""
        assert is_playlist_not_found(_http_error(404, b"playlistNotFound"))

    def test_other_404(self) -> None:
        """A 404 with another reason is not."""
        assert not is_playlist_not_found(_http_error(404, b"channelNotFound"))

    def test_non_http_error(self) -> None:
        """A non-HttpError exception is not."""
        assert not is_playlist_not_found(ValueError("playlistNotFound"))


class TestNormalizeHandle:
    """Tests for the customUrl -> @handle rule."""

    @pytest.mark.parametrize(
        ("custom_url", "expected"),
        [("@name", "@name"), ("name", "@name"), ("", None), (None, None)],
    )
    def test_normalizes(self, custom_url: str | None, expected: str | None) -> None:
        """customUrl gains a leading @; empty values become None."""
        assert normalize_handle(custom_url) == expected


class TestResolveChannel:
    """Tests for the handle -> id -> title-search resolution chain."""

    def test_handle_uses_for_handle_selector(self) -> None:
        """A handle is looked up with forHandle and returns a full resolution."""
        youtube = _youtube(_channel())

        resolved = resolve_channel(youtube, handle="@ericwtech")

        assert _list_kwargs(youtube) == [
            {"part": "snippet,contentDetails", "forHandle": "@ericwtech"}
        ]
        assert resolved == youtube_api.ResolvedChannel(
            id=CHANNEL_ID,
            handle="@ericwtech",
            title="Eric Tech",
            uploads_playlist_id=UPLOADS_ID,
            strategy="handle",
        )

    def test_channel_id_uses_id_selector(self) -> None:
        """A channel ID alone is looked up with id."""
        youtube = _youtube(_channel())

        resolved = resolve_channel(youtube, channel_id=CHANNEL_ID)

        assert _list_kwargs(youtube) == [
            {"part": "snippet,contentDetails", "id": CHANNEL_ID}
        ]
        assert resolved is not None and resolved.strategy == "id"

    def test_falls_back_from_handle_to_id(self) -> None:
        """When the handle finds nothing, the stored ID is tried next."""
        youtube = _youtube(NO_ITEMS, _channel())

        resolved = resolve_channel(youtube, handle="@gone", channel_id=CHANNEL_ID)

        assert resolved is not None and resolved.strategy == "id"
        assert len(_list_kwargs(youtube)) == 2

    def test_non_at_handle_is_ignored(self) -> None:
        """A handle without a leading @ is not sent as forHandle."""
        youtube = _youtube(_channel())

        resolve_channel(youtube, handle="ericwtech", channel_id=CHANNEL_ID)

        assert _list_kwargs(youtube) == [
            {"part": "snippet,contentDetails", "id": CHANNEL_ID}
        ]

    def test_custom_url_without_at_is_prefixed(self) -> None:
        """A customUrl returned without "@" is normalised to a handle."""
        youtube = _youtube(_channel(custom_url="ericwtech"))

        resolved = resolve_channel(youtube, channel_id=CHANNEL_ID)

        assert resolved is not None and resolved.handle == "@ericwtech"

    def test_api_handle_casing_wins_over_given_handle(self) -> None:
        """The API's customUrl casing is preferred over the handle that was passed."""
        youtube = _youtube(_channel(custom_url="@ericwtech"))

        resolved = resolve_channel(youtube, handle="@EricWTech")

        assert resolved is not None and resolved.handle == "@ericwtech"

    def test_missing_custom_url_falls_back_to_given_handle(self) -> None:
        """With no customUrl, a channel resolved by handle keeps that handle."""
        youtube = _youtube(_channel(custom_url=None))

        resolved = resolve_channel(youtube, handle="@ericwtech")

        assert resolved is not None and resolved.handle == "@ericwtech"

    def test_nothing_found_returns_none(self) -> None:
        """No step finding a channel returns None."""
        youtube = _youtube(NO_ITEMS, NO_ITEMS)

        assert resolve_channel(youtube, handle="@nobody", channel_id=CHANNEL_ID) is None

    def test_missing_uploads_playlist_is_rejected(self) -> None:
        """A channel with no uploads playlist is not accepted."""
        youtube = _youtube(_channel(uploads=None))

        assert resolve_channel(youtube, channel_id=CHANNEL_ID) is None

    def test_unqueryable_uploads_playlist_falls_through(self) -> None:
        """A candidate whose playlist 404s is rejected and the next step is tried."""
        youtube = _youtube(_channel(channel_id="UCwrong"), _channel())
        youtube.playlistItems.return_value.list.return_value.execute.side_effect = [
            _http_error(404, b"playlistNotFound"),
            {"items": []},
        ]

        resolved = resolve_channel(youtube, handle="@ericwtech", channel_id=CHANNEL_ID)

        assert resolved is not None
        assert (resolved.id, resolved.strategy) == (CHANNEL_ID, "id")

    def test_other_probe_errors_propagate(self) -> None:
        """Probe errors other than playlistNotFound are raised."""
        youtube = _youtube(_channel())
        youtube.playlistItems.return_value.list.return_value.execute.side_effect = (
            _http_error(403, b"forbidden")
        )

        with pytest.raises(googleapiclient.errors.HttpError):
            resolve_channel(youtube, channel_id=CHANNEL_ID)

    def test_no_title_means_no_search(self) -> None:
        """The 100-unit search only runs when a title is given."""
        youtube = _youtube(NO_ITEMS)

        assert resolve_channel(youtube, handle="@gone") is None
        youtube.search.assert_not_called()

    def test_title_search_accepts_matching_title(self) -> None:
        """A search hit whose title matches (ignoring case) is accepted."""
        youtube = _youtube(NO_ITEMS, _channel(title="Eric Tech"))
        youtube.search.return_value.list.return_value.execute.return_value = {
            "items": [{"id": {"channelId": CHANNEL_ID}}]
        }

        resolved = resolve_channel(youtube, handle="@gone", title="eric tech")

        assert resolved is not None
        assert (resolved.id, resolved.strategy) == (CHANNEL_ID, "search")

    def test_title_search_rejects_different_title(self) -> None:
        """A search hit for a differently titled channel is rejected."""
        youtube = _youtube(NO_ITEMS, _channel(title="Eric Tech Clips"))
        youtube.search.return_value.list.return_value.execute.return_value = {
            "items": [{"id": {"channelId": CHANNEL_ID}}]
        }

        assert resolve_channel(youtube, handle="@gone", title="Eric Tech") is None
