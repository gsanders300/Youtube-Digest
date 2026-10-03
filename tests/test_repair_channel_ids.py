"""Tests for repair_channel_ids.repair (which entries are resolved and corrected)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import repair_channel_ids
from channel_list import Channel
from youtube_api import ResolvedChannel

STALE = Channel(
    handle="@wes", title="Wes Roth", id="UCwrong", uploads_playlist_id="UUwrong"
)
RESOLVED = ResolvedChannel(
    id="UCright",
    handle="@wes",
    title="Wes Roth",
    uploads_playlist_id="UUright",
    strategy="search",
)


class TestRepair:
    """Tests for repair."""

    def test_wrong_ids_are_corrected_with_full_resolution_chain(self) -> None:
        """A stale entry is resolved with handle, ID, and title, then corrected."""
        channels = [STALE.model_copy()]
        youtube = MagicMock()

        with patch(
            "repair_channel_ids.resolve_channel", return_value=RESOLVED
        ) as resolve:
            changed, unresolved = repair_channel_ids.repair(channels, youtube)

        resolve.assert_called_once_with(
            youtube, handle="@wes", channel_id="UCwrong", title="Wes Roth"
        )
        assert (changed, unresolved) == (1, [])
        assert (channels[0].id, channels[0].uploads_playlist_id) == (
            "UCright",
            "UUright",
        )

    def test_correct_entry_is_unchanged(self) -> None:
        """An entry that already matches its resolution is not counted."""
        channels = [
            STALE.model_copy(update={"id": "UCright", "uploads_playlist_id": "UUright"})
        ]

        with patch("repair_channel_ids.resolve_channel", return_value=RESOLVED):
            assert repair_channel_ids.repair(channels, MagicMock()) == (0, [])

    def test_unresolved_entry_is_reported_and_left_alone(self) -> None:
        """An entry no step can resolve is listed as unresolved and not modified."""
        channels = [STALE.model_copy()]

        with patch("repair_channel_ids.resolve_channel", return_value=None):
            assert repair_channel_ids.repair(channels, MagicMock()) == (0, ["@wes"])
        assert channels[0] == STALE

    def test_entry_without_stored_id_is_skipped(self) -> None:
        """A bare "@handle" entry has nothing to repair and costs no API calls."""
        with patch("repair_channel_ids.resolve_channel") as resolve:
            repair_channel_ids.repair([Channel(handle="@bare")], MagicMock())

        resolve.assert_not_called()
