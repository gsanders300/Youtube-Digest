"""Reads, writes, and merges channels.yml, the list of monitored channels.

This module is the only code that knows the file's entry shapes: a bare
"@handle" string, or a dict with handle, title, id, uploads_playlist_id, and
digest (default True). Unknown keys in a dict entry are preserved on rewrite.
"""

from __future__ import annotations

import yaml
from pydantic import BaseModel, ConfigDict

CHANNELS_FILE = "channels.yml"


class Channel(BaseModel):
    """One channels.yml entry."""

    model_config = ConfigDict(extra="allow")

    handle: str | None = None
    title: str | None = None
    id: str | None = None
    uploads_playlist_id: str | None = None
    digest: bool = True


def load(path: str = CHANNELS_FILE) -> list[Channel]:
    """Loads every entry from a channels file.

    Args:
        path: The channels file to read.

    Returns:
        The entries in file order; a bare "@handle" string becomes Channel(handle=...).
    """
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    return [
        Channel(handle=entry) if isinstance(entry, str) else Channel(**entry)
        for entry in data.get("channels") or []
    ]


def save(channels: list[Channel], path: str = CHANNELS_FILE) -> None:
    """Writes entries to a channels file, replacing its contents.

    An entry that carries only a handle (and the default digest flag) is written
    as a bare "@handle" string; every other entry is written as a dict in the
    canonical key order, followed by any unknown keys it was loaded with.

    Args:
        channels: The entries to write, in order.
        path: The channels file to write.
    """
    entries: list[str | dict] = []
    for channel in channels:
        data = channel.model_dump(exclude_none=True)
        is_bare = channel.handle and data == {"handle": channel.handle, "digest": True}
        entries.append(channel.handle if is_bare else data)
    with open(path, "w") as f:
        yaml.dump({"channels": entries}, f, default_flow_style=False, sort_keys=False)


def _index_of(channels: list[Channel], channel: Channel) -> int | None:
    """Returns the index of the entry matching by ID, else by handle ignoring case."""
    if channel.id:
        for index, existing in enumerate(channels):
            if existing.id == channel.id:
                return index
    handle = (channel.handle or "").casefold()
    if handle:
        for index, existing in enumerate(channels):
            if (existing.handle or "").casefold() == handle:
                return index
    return None


def add(channels: list[Channel], channel: Channel) -> bool:
    """Appends an entry unless the channel is already listed.

    Args:
        channels: The loaded entries, modified in place.
        channel: The new entry.

    Returns:
        True if the entry was appended, False if an entry with the same ID or
        handle (case-insensitive) is already present.
    """
    if _index_of(channels, channel) is not None:
        return False
    channels.append(channel)
    return True


def merge_subscriptions(
    existing: list[Channel], subscribed: list[Channel]
) -> list[Channel]:
    """Merges the user's current subscriptions into the existing entries.

    Every existing entry is kept, in its original position, with its digest
    flag and any unknown keys. An existing entry that matches a subscription
    (by ID, then by handle ignoring case) takes the subscription's handle,
    title, ID, and uploads playlist. Subscriptions with no match are appended.

    Args:
        existing: The entries currently in the channels file.
        subscribed: One entry per current subscription, fresh from the API.

    Returns:
        The merged entries.
    """
    merged = list(existing)
    for subscription in subscribed:
        index = _index_of(merged, subscription)
        if index is None:
            merged.append(subscription)
            continue
        fresh = subscription.model_dump(
            include={"handle", "title", "id", "uploads_playlist_id"}, exclude_none=True
        )
        merged[index] = merged[index].model_copy(update=fresh)
    return merged
