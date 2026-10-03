"""Tests for add_channel.py (URL parsing and the end-to-end main flow)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import add_channel

CHANNEL_ID = "UCOXRjenlq9PmlTqd_JhAbMQ"
UPLOADS_ID = "UUOXRjenlq9PmlTqd_JhAbMQ"


def _channel_response(
    channel_id: str = CHANNEL_ID,
    title: str = "Eric Tech",
    custom_url: str | None = "@ericwtech",
    uploads: str | None = UPLOADS_ID,
) -> dict[str, Any]:
    """Builds a fake channels().list response body."""
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


# ---------------------------------------------------------------------------
# parse_channel_url
# ---------------------------------------------------------------------------


class TestParseChannelUrl:
    """Tests for parse_channel_url."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/@ericwtech",
            "https://www.youtube.com/@ericwtech/videos",
            "https://www.youtube.com/@ericwtech/",
            "https://www.youtube.com/@ericwtech?si=abc",
            "https://youtube.com/@ericwtech",
            "https://m.youtube.com/@ericwtech",
            "http://www.youtube.com/@ericwtech",
            "www.youtube.com/@ericwtech",
            "@ericwtech",
            "  https://www.youtube.com/@ericwtech  ",
        ],
    )
    def test_accepts_handle_forms(self, url: str) -> None:
        """Handle URLs and bare handles resolve to a handle selector."""
        assert add_channel.parse_channel_url(url) == ("handle", "@ericwtech")

    @pytest.mark.parametrize(
        "url",
        [
            f"https://www.youtube.com/channel/{CHANNEL_ID}",
            f"https://www.youtube.com/channel/{CHANNEL_ID}/videos?view=0",
            f"https://youtube.com/channel/{CHANNEL_ID}",
            CHANNEL_ID,
        ],
    )
    def test_accepts_channel_id_forms(self, url: str) -> None:
        """Channel URLs and bare UC IDs resolve to an id selector."""
        assert add_channel.parse_channel_url(url) == ("id", CHANNEL_ID)

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/user/ericwtech",
            "https://www.youtube.com/c/EricTech",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/",
            "https://example.com/@ericwtech",
            "https://www.youtube.com/channel/UCtooshort",
            "@ab",
            "",
        ],
    )
    def test_rejects_other_forms(self, url: str) -> None:
        """Anything other than a handle or channel URL raises ValueError."""
        with pytest.raises(ValueError):
            add_channel.parse_channel_url(url)


def _entry(handle: str = "@ericwtech", channel_id: str = CHANNEL_ID) -> dict[str, Any]:
    """Builds a channels.yml entry for a test channel."""
    return {
        "handle": handle,
        "title": "Eric Tech",
        "id": channel_id,
        "uploads_playlist_id": "UU" + channel_id[2:],
        "digest": True,
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    """End-to-end tests for main() against a temporary channels.yml."""

    @pytest.fixture
    def repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Creates a two-entry channels.yml in a temp cwd with an API key set."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
        channels = [
            _entry("@first", "UC000000000000000000000A"),
            {**_entry("@second", "UC000000000000000000000B"), "digest": False},
        ]
        (tmp_path / "channels.yml").write_text(
            yaml.dump({"channels": channels}, default_flow_style=False, sort_keys=False)
        )
        return tmp_path

    def _run(self, url: str, response: dict[str, Any]) -> None:
        """Runs main() with a mocked YouTube client returning the given response."""
        youtube = MagicMock()
        youtube.channels.return_value.list.return_value.execute.return_value = response
        with patch("add_channel.googleapiclient.discovery.build", return_value=youtube):
            add_channel.main([url])

    def test_appends_new_channel(self, repo: Path) -> None:
        """A new channel is appended with the canonical key order."""
        self._run("https://www.youtube.com/@ericwtech", _channel_response())

        data = yaml.safe_load((repo / "channels.yml").read_text())
        channels = data["channels"]
        assert len(channels) == 3
        assert channels[0]["handle"] == "@first"
        assert channels[1]["digest"] is False
        assert channels[2] == _entry()
        assert list(channels[2]) == [
            "handle",
            "title",
            "id",
            "uploads_playlist_id",
            "digest",
        ]

    def test_duplicate_leaves_file_unchanged(self, repo: Path) -> None:
        """An already-listed channel exits normally without rewriting the file."""
        before = (repo / "channels.yml").read_bytes()

        self._run("@first", _channel_response(channel_id="UC000000000000000000000A"))

        assert (repo / "channels.yml").read_bytes() == before

    def test_bad_url_exits_1(self, repo: Path) -> None:
        """An unrecognised URL exits with status 1 before touching the API."""
        with pytest.raises(SystemExit) as exc_info:
            self._run("https://www.youtube.com/watch?v=abc", _channel_response())
        assert exc_info.value.code == 1

    def test_unresolved_channel_exits_1(self, repo: Path) -> None:
        """A channel the API cannot find exits with status 1."""
        with pytest.raises(SystemExit) as exc_info:
            self._run("@nobody", {"items": []})
        assert exc_info.value.code == 1

    def test_missing_api_key_exits_1(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing YOUTUBE_API_KEY exits with status 1."""
        monkeypatch.delenv("YOUTUBE_API_KEY")
        with pytest.raises(SystemExit) as exc_info:
            add_channel.main(["@ericwtech"])
        assert exc_info.value.code == 1
