"""Tests for the digest lookback window: DIGEST_SINCE and its 24-hour fallback."""

from __future__ import annotations

import datetime
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import isodate
import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from digest import LOOKBACK_HOURS, YouTubeDigest, lookback_cutoff

EASTERN = ZoneInfo("America/New_York")
NOW = datetime.datetime(2026, 11, 9, 18, 0, tzinfo=EASTERN)


def _iso_utc(moment: datetime.datetime) -> str:
    """Formats a time the way the GitHub API reports startedAt."""
    return moment.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _item(video_id: str, published: datetime.datetime) -> dict:
    """Builds a minimal playlistItems.list item."""
    return {
        "contentDetails": {"videoId": video_id, "videoPublishedAt": _iso_utc(published)},
        "snippet": {"publishedAt": _iso_utc(published)},
    }


class TestLookbackCutoff:
    """Tests for lookback_cutoff()."""

    @pytest.mark.parametrize("value", [None, ""])
    def test_unset_or_empty_falls_back_to_lookback_hours(
        self, monkeypatch: pytest.MonkeyPatch, value: str | None
    ) -> None:
        """No previous successful run means the last LOOKBACK_HOURS hours."""
        if value is None:
            monkeypatch.delenv("DIGEST_SINCE", raising=False)
        else:
            monkeypatch.setenv("DIGEST_SINCE", value)
        assert lookback_cutoff(NOW) == NOW - datetime.timedelta(hours=LOOKBACK_HOURS)

    def test_uses_digest_since_converted_to_now_timezone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A UTC start time from the GitHub API becomes the cutoff, in Eastern time."""
        monkeypatch.setenv("DIGEST_SINCE", "2026-11-07T23:00:37Z")
        cutoff = lookback_cutoff(NOW)
        assert cutoff == datetime.datetime(2026, 11, 7, 23, 0, 37, tzinfo=datetime.UTC)
        assert cutoff.tzinfo is EASTERN
        assert (cutoff.hour, cutoff.minute) == (18, 0)

    def test_rejects_value_without_timezone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A naive time is ambiguous, so it fails instead of guessing a zone."""
        monkeypatch.setenv("DIGEST_SINCE", "2026-11-07T23:00:37")
        with pytest.raises(ValueError, match="no timezone"):
            lookback_cutoff(NOW)

    def test_rejects_unparseable_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A malformed value fails instead of silently using 24 hours."""
        monkeypatch.setenv("DIGEST_SINCE", "yesterday")
        with pytest.raises(isodate.ISO8601Error):
            lookback_cutoff(NOW)


class TestGetRecentVideosWindow:
    """Tests that get_recent_videos() filters by the run's cutoff."""

    def _fetch(self, items: list[dict]) -> list[str]:
        digest = YouTubeDigest(youtube=MagicMock(), genai_client=MagicMock())
        with patch("digest.execute", return_value={"items": items}):
            videos = digest.get_recent_videos("UU001")
        return [v["contentDetails"]["videoId"] for v in videos]

    def test_missed_run_keeps_videos_older_than_24_hours(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After a missed run, videos since the last success are kept, not dropped."""
        since = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=72)
        monkeypatch.setenv("DIGEST_SINCE", _iso_utc(since))
        items = [
            _item("after", since + datetime.timedelta(hours=1)),
            _item("before", since - datetime.timedelta(hours=1)),
        ]
        assert self._fetch(items) == ["after"]

    def test_without_digest_since_uses_last_24_hours(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without DIGEST_SINCE, a 48-hour-old video is outside the window."""
        monkeypatch.delenv("DIGEST_SINCE", raising=False)
        now = datetime.datetime.now(datetime.UTC)
        items = [
            _item("recent", now - datetime.timedelta(hours=1)),
            _item("stale", now - datetime.timedelta(hours=48)),
        ]
        assert self._fetch(items) == ["recent"]
