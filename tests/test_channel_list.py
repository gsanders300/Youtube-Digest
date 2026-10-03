"""Tests for channel_list: loading, saving, adding, and merging channels.yml entries."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import channel_list
from channel_list import Channel

REPO_CHANNELS_FILE = Path(__file__).parent.parent / "channels.yml"


def _write(path: Path, channels: list) -> str:
    """Writes a channels file and returns its path as a string."""
    path.write_text(yaml.dump({"channels": channels}, sort_keys=False))
    return str(path)


def _channel(
    handle: str = "@ericwtech", channel_id: str = "UC001", **kwargs
) -> Channel:
    """Builds a full Channel entry."""
    return Channel(
        handle=handle,
        title=kwargs.pop("title", "Eric Tech"),
        id=channel_id,
        uploads_playlist_id=kwargs.pop("uploads_playlist_id", "UU" + channel_id[2:]),
        **kwargs,
    )


class TestLoad:
    """Tests for load."""

    def test_both_entry_shapes(self, tmp_path: Path) -> None:
        """Bare "@handle" strings and dicts both load as Channel entries."""
        path = _write(tmp_path / "c.yml", ["@bare", {"handle": "@dict", "id": "UC1"}])

        channels = channel_list.load(path)

        assert channels == [Channel(handle="@bare"), Channel(handle="@dict", id="UC1")]

    def test_digest_defaults_to_true(self, tmp_path: Path) -> None:
        """An entry without a digest field is opted in; digest: false is kept."""
        path = _write(
            tmp_path / "c.yml", [{"id": "UC1"}, {"id": "UC2", "digest": False}]
        )

        assert [c.digest for c in channel_list.load(path)] == [True, False]

    def test_empty_file(self, tmp_path: Path) -> None:
        """An empty file loads as an empty list."""
        path = tmp_path / "c.yml"
        path.write_text("")

        assert channel_list.load(str(path)) == []


class TestSave:
    """Tests for save."""

    def test_bare_handle_round_trips_as_string(self, tmp_path: Path) -> None:
        """A handle-only entry is written back as a bare string."""
        path = _write(tmp_path / "c.yml", ["@bare"])

        channel_list.save(channel_list.load(path), path)

        assert yaml.safe_load(Path(path).read_text()) == {"channels": ["@bare"]}

    def test_canonical_key_order_and_unknown_keys_kept(self, tmp_path: Path) -> None:
        """Dict entries use the canonical key order, then any unknown keys."""
        path = str(tmp_path / "c.yml")

        channel_list.save([_channel(digest=False, note="keep me")], path)

        (entry,) = yaml.safe_load(Path(path).read_text())["channels"]
        assert list(entry) == [
            "handle",
            "title",
            "id",
            "uploads_playlist_id",
            "digest",
            "note",
        ]
        assert entry["digest"] is False

    def test_repo_channels_file_round_trips(self, tmp_path: Path) -> None:
        """Saving the real channels.yml loses no entries, fields, or digest flags."""
        original = channel_list.load(str(REPO_CHANNELS_FILE))
        path = str(tmp_path / "c.yml")

        channel_list.save(original, path)

        assert channel_list.load(path) == original
        assert yaml.safe_load(Path(path).read_text()) == yaml.safe_load(
            REPO_CHANNELS_FILE.read_text()
        )


class TestAdd:
    """Tests for add."""

    def test_appends_to_end(self) -> None:
        """A new channel is appended."""
        channels = [_channel("@first", "UC000")]

        assert channel_list.add(channels, _channel()) is True
        assert channels[-1] == _channel()

    def test_duplicate_id_is_rejected(self) -> None:
        """A channel with an already-listed ID is not appended."""
        channels = [_channel("@renamed", "UC001")]

        assert channel_list.add(channels, _channel()) is False
        assert len(channels) == 1

    def test_duplicate_handle_is_rejected_case_insensitively(self) -> None:
        """A channel whose handle differs only by case is not appended."""
        channels = [_channel("@EricWTech", "UC999")]

        assert channel_list.add(channels, _channel()) is False

    def test_bare_handle_entry_blocks_duplicate(self) -> None:
        """A bare "@handle" entry counts as the same channel."""
        channels = [Channel(handle="@ericwtech")]

        assert channel_list.add(channels, _channel()) is False


class TestMergeSubscriptions:
    """Tests for merge_subscriptions."""

    def test_new_subscription_is_appended_opted_in(self) -> None:
        """A subscription not yet listed is appended with digest: true."""
        merged = channel_list.merge_subscriptions(
            [_channel("@a", "UC0A")], [_channel()]
        )

        assert merged == [_channel("@a", "UC0A"), _channel()]
        assert merged[-1].digest is True

    def test_opt_out_is_kept_when_matched_by_id(self) -> None:
        """An existing digest: false survives even if the handle changed."""
        existing = [_channel("@oldname", "UC001", digest=False)]

        (merged,) = channel_list.merge_subscriptions(existing, [_channel("@newname")])

        assert merged.digest is False
        assert merged.handle == "@newname"

    def test_opt_out_is_kept_when_matched_by_handle_ignoring_case(self) -> None:
        """An existing entry with no ID matches a subscription by handle, any case."""
        existing = [Channel(handle="@EricWTech", digest=False)]

        (merged,) = channel_list.merge_subscriptions(existing, [_channel()])

        assert merged.digest is False
        assert merged.id == "UC001"

    def test_unsubscribed_entries_are_kept_in_place(self) -> None:
        """Entries that are not current subscriptions stay, in their original order."""
        existing = [_channel("@manual", "UC0M"), _channel("@sub", "UC0S")]

        merged = channel_list.merge_subscriptions(existing, [_channel("@sub", "UC0S")])

        assert [c.handle for c in merged] == ["@manual", "@sub"]

    def test_subscription_refreshes_fields_but_keeps_unknown_keys(self) -> None:
        """Matched entries take fresh API fields and keep any unknown keys."""
        existing = [
            _channel(title="Old Title", uploads_playlist_id="UUstale", note="x")
        ]

        (merged,) = channel_list.merge_subscriptions(existing, [_channel()])

        assert merged.title == "Eric Tech"
        assert merged.uploads_playlist_id == "UU001"
        assert merged.model_dump()["note"] == "x"

    def test_missing_handle_does_not_erase_existing_handle(self) -> None:
        """A subscription with no customUrl keeps the stored handle."""
        existing = [_channel("@kept", "UC001")]

        (merged,) = channel_list.merge_subscriptions(
            existing, [_channel(None, "UC001")]
        )

        assert merged.handle == "@kept"
