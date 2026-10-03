#!/usr/bin/env python3
"""Repair channel IDs in channels.yml by re-resolving each entry from the API.

Some channels.yml entries can carry an incorrect channel ID (for example one
added by hand), whose derived uploads playlist then returns a 404
"playlistNotFound" and drops the channel from the digest. This utility reads
channels.yml, resolves the correct channel ID and uploads playlist ID for every
entry with a stored ID (trying the @handle, then the stored channel ID, then a
search by title), and reports or applies the corrections.

It defaults to a dry run and only rewrites channels.yml when passed --write.

Usage:
    YOUTUBE_API_KEY=... uv run python repair_channel_ids.py            # dry run
    YOUTUBE_API_KEY=... uv run python repair_channel_ids.py --write    # apply
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import googleapiclient.discovery

import channel_list
import environment
from youtube_api import resolve_channel


def repair(channels: list[channel_list.Channel], youtube: Any) -> tuple[int, list[str]]:
    """Resolves and corrects channel IDs in place, reporting each entry.

    Args:
        channels: The entries loaded from channels.yml, corrected in place.
            Entries with no stored channel ID (such as bare "@handle" entries)
            are skipped because they carry no IDs to repair.
        youtube: A YouTube Data API v3 client.

    Returns:
        A (changed_count, unresolved_labels) tuple.
    """
    changed = 0
    unresolved: list[str] = []

    for channel in channels:
        if not channel.id:
            # Nothing stored to repair; the digest resolves these from the
            # handle at runtime.
            continue

        old_id = channel.id
        old_uploads = channel.uploads_playlist_id
        label = channel.handle or old_id

        try:
            resolved = resolve_channel(
                youtube, handle=channel.handle, channel_id=old_id, title=channel.title
            )
        except Exception as e:
            print(f"  ERROR  {label}: {e}")
            unresolved.append(label)
            continue

        if not resolved:
            print(f"  UNRESOLVED  {label} (verify its @handle / channel URL)")
            unresolved.append(label)
            continue

        if resolved.id != old_id or resolved.uploads_playlist_id != old_uploads:
            print(
                f"  FIX  {label} (via {resolved.strategy}): id {old_id} -> "
                f"{resolved.id}, uploads {old_uploads} -> {resolved.uploads_playlist_id}"
            )
            channel.id = resolved.id
            channel.uploads_playlist_id = resolved.uploads_playlist_id
            changed += 1
        else:
            print(f"  ok   {label}")

    return changed, unresolved


def main() -> None:
    """Entry point: resolve channel IDs and optionally rewrite channels.yml."""
    parser = argparse.ArgumentParser(
        description="Re-resolve and correct channel IDs in channels.yml."
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Apply corrections to channels.yml (default: dry run only).",
    )
    args = parser.parse_args()

    api_key = environment.youtube_api_key()
    if not api_key:
        print("YOUTUBE_API_KEY is not set in the environment. Aborting.")
        sys.exit(1)

    channels = channel_list.load()
    youtube = googleapiclient.discovery.build("youtube", "v3", developerKey=api_key)

    print(
        f"Checking {len(channels)} channel entries in {channel_list.CHANNELS_FILE}..."
    )
    changed, unresolved = repair(channels, youtube)

    print(
        f"\n{changed} channel(s) need correction; {len(unresolved)} could not "
        "be resolved."
    )
    if unresolved:
        print("Unresolved (left unchanged): " + ", ".join(unresolved))

    if not args.write:
        print(
            "\nDry run: no changes written. Re-run with --write to apply the "
            "corrections above."
        )
        return

    if changed == 0:
        print("\nNothing to write; channels.yml is already correct.")
        return

    channel_list.save(channels)
    print(f"\nWrote {changed} correction(s) to {channel_list.CHANNELS_FILE}.")


if __name__ == "__main__":
    main()
