#!/usr/bin/env python3
"""Add a YouTube channel to channels.yml from its channel URL.

Given a channel URL (or a bare @handle / UC... channel ID), this utility looks
up the channel's handle, title, channel ID and uploads playlist ID with a single
YouTube Data API call, verifies the uploads playlist is queryable, and appends
the entry to channels.yml with digest: true. It does nothing if the channel is
already listed.

Accepted forms:
    https://www.youtube.com/@Name[/videos][?query]
    https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx
    @Name
    UCxxxxxxxxxxxxxxxxxxxxxx

Usage:
    YOUTUBE_API_KEY=... uv run python add_channel.py https://www.youtube.com/@Name
"""

from __future__ import annotations

import argparse
import re
import sys

import googleapiclient.discovery
import googleapiclient.errors

import channel_list
import environment
from youtube_api import resolve_channel

_URL_PREFIX = re.compile(r"^(?:https?://)?(?:(?:www|m)\.)?youtube\.com/", re.IGNORECASE)
_HANDLE = re.compile(r"^@[A-Za-z0-9._-]{3,30}$")
_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")

_ACCEPTED_FORMS = (
    "Expected a channel URL like https://www.youtube.com/@Name or "
    "https://www.youtube.com/channel/UC..., or a bare @Name / UC... ID."
)


def parse_channel_url(url: str) -> tuple[str, str]:
    """Extracts a channel selector from a YouTube channel URL.

    Args:
        url: A channel URL, a bare @handle, or a bare UC... channel ID.

    Returns:
        A (kind, value) tuple where kind is "handle" (value is "@Name") or
        "id" (value is the UC... channel ID).

    Raises:
        ValueError: If the input is not one of the accepted channel forms.
    """
    url = url.strip()
    prefix = _URL_PREFIX.match(url)

    if prefix is None:
        if _HANDLE.match(url):
            return "handle", url
        if _CHANNEL_ID.match(url):
            return "id", url
        raise ValueError(f"Unrecognized channel URL {url!r}. {_ACCEPTED_FORMS}")

    path = re.split(r"[?#]", url[prefix.end() :], maxsplit=1)[0]
    segments = [segment for segment in path.split("/") if segment]

    if segments and _HANDLE.match(segments[0]):
        return "handle", segments[0]
    if (
        len(segments) >= 2
        and segments[0] == "channel"
        and _CHANNEL_ID.match(segments[1])
    ):
        return "id", segments[1]
    raise ValueError(f"Unrecognized channel URL {url!r}. {_ACCEPTED_FORMS}")


def main(argv: list[str] | None = None) -> None:
    """Entry point: resolve a channel URL and append it to channels.yml."""
    parser = argparse.ArgumentParser(
        description="Look up a YouTube channel by URL and add it to channels.yml."
    )
    parser.add_argument("url", help="Channel URL, @handle, or UC... channel ID.")
    args = parser.parse_args(argv)

    api_key = environment.youtube_api_key()
    if not api_key:
        print("YOUTUBE_API_KEY is not set in the environment. Aborting.")
        sys.exit(1)

    try:
        kind, value = parse_channel_url(args.url)
    except ValueError as e:
        print(e)
        sys.exit(1)

    youtube = googleapiclient.discovery.build("youtube", "v3", developerKey=api_key)
    selector = {"handle": value} if kind == "handle" else {"channel_id": value}

    print(f"Resolving {value}...")
    try:
        resolved = resolve_channel(youtube, **selector)
    except googleapiclient.errors.HttpError as e:
        print(f"YouTube API error while resolving {value}: {e}")
        sys.exit(1)

    if resolved is None:
        print(
            f"Could not resolve {value} to a channel with a working uploads playlist."
        )
        sys.exit(1)

    channel = channel_list.Channel(
        handle=resolved.handle,
        title=resolved.title,
        id=resolved.id,
        uploads_playlist_id=resolved.uploads_playlist_id,
    )
    channels = channel_list.load()

    label = f"{channel.handle} ({channel.title}, {channel.id})"
    if not channel_list.add(channels, channel):
        print(f"{label} is already in {channel_list.CHANNELS_FILE}; nothing to do.")
        return

    channel_list.save(channels)
    print(f"Added {label} to {channel_list.CHANNELS_FILE}.")


if __name__ == "__main__":
    main()
