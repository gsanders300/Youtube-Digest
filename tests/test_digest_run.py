"""Tests for YouTubeDigest.run(): subject line video count, skipped-channel
reporting, and batch summarization wiring across multiple channels."""

from __future__ import annotations

import datetime as _datetime_module
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import googleapiclient.errors
import httplib2
import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from digest import VideoDetail, YouTubeDigest
from youtube_api import ResolvedChannel


class _PortableDateTime(_datetime_module.datetime):
    """A datetime subclass whose strftime tolerates Linux-only no-pad directives.

    digest.py's date formatting uses %-d/%-I (no leading zero), which are
    glibc/BSD strftime extensions available on the Linux CI runner this
    project ships to, but not supported by Windows' C runtime. This shim lets
    run() be exercised in this test suite on any OS without changing the
    format strings used in the actual shipped code.
    """

    def strftime(self, fmt: str) -> str:
        portable_fmt = fmt.replace("%-d", "%d").replace("%-I", "%I")
        return super().strftime(portable_fmt)


@pytest.fixture(autouse=True)
def _portable_datetime_for_windows():
    """Patches digest.py's datetime.datetime for cross-platform strftime."""
    with patch("digest.datetime.datetime", _PortableDateTime):
        yield


def _make_digest() -> YouTubeDigest:
    """Builds a YouTubeDigest instance without hitting any real APIs."""
    return YouTubeDigest(youtube=MagicMock(), genai_client=MagicMock())


CHANNELS_YML = """
channels:
- id: UC001
  title: Channel A
  uploads_playlist_id: UU001
  digest: true
- id: UC002
  title: Channel B
  uploads_playlist_id: UU002
  digest: true
"""


def _video_item(video_id: str, channel_title: str) -> dict:
    """Builds a fake playlistItems() response item."""
    return {
        "contentDetails": {"videoId": video_id},
        "snippet": {"channelTitle": channel_title},
    }


def _video_detail(video_id: str, title: str) -> VideoDetail:
    """Builds a fake VideoDetail for a given video ID."""
    return VideoDetail(
        title=title,
        description=f"Description for {title}",
        duration="5:00",
        link=f"https://www.youtube.com/watch?v={video_id}",
    )


def _http_error(status: int, reason: str) -> googleapiclient.errors.HttpError:
    """Builds a googleapiclient HttpError with a given HTTP status and reason."""
    resp = httplib2.Response({"status": status})
    content = json.dumps(
        {"error": {"code": status, "message": reason, "errors": [{"reason": reason}]}}
    ).encode("utf-8")
    return googleapiclient.errors.HttpError(resp, content)


class TestRunSubjectLine:
    """Tests for the video-count-aware email subject line."""

    def test_subject_includes_video_count(self) -> None:
        """The subject should mention the total number of new videos found."""
        digest = _make_digest()
        digest.get_recent_videos = MagicMock(
            side_effect=[
                [_video_item("vid1", "Channel A")],
                [_video_item("vid2", "Channel B")],
            ]
        )
        digest.get_video_details = MagicMock(
            side_effect=[
                {"vid1": _video_detail("vid1", "Video One")},
                {"vid2": _video_detail("vid2", "Video Two")},
            ]
        )
        digest.summarize_descriptions_batch = MagicMock()
        digest.send_email = MagicMock()

        with patch("builtins.open", mock_open(read_data=CHANNELS_YML)):
            digest.run()

        digest.send_email.assert_called_once()
        _html, subject = digest.send_email.call_args.args
        assert "(2 new videos)" in subject

    def test_singular_video_count_uses_singular_word(self) -> None:
        """Exactly one new video should say '1 new video', not 'videos'."""
        digest = _make_digest()
        digest.get_recent_videos = MagicMock(
            side_effect=[[_video_item("vid1", "Channel A")], []]
        )
        digest.get_video_details = MagicMock(
            return_value={"vid1": _video_detail("vid1", "Video One")}
        )
        digest.summarize_descriptions_batch = MagicMock()
        digest.send_email = MagicMock()

        with patch("builtins.open", mock_open(read_data=CHANNELS_YML)):
            digest.run()

        _html, subject = digest.send_email.call_args.args
        assert "(1 new video)" in subject
        assert "videos)" not in subject

    def test_zero_videos_subject_says_no_new_videos(self) -> None:
        """No new videos should be reflected in the subject and skip batching."""
        digest = _make_digest()
        digest.get_recent_videos = MagicMock(return_value=[])
        digest.summarize_descriptions_batch = MagicMock()
        digest.send_email = MagicMock()

        with patch("builtins.open", mock_open(read_data=CHANNELS_YML)):
            digest.run()

        _html, subject = digest.send_email.call_args.args
        assert "(no new videos)" in subject
        digest.summarize_descriptions_batch.assert_not_called()


class TestRunSkippedChannelReporting:
    """Tests for surfacing per-channel failures in the digest email itself."""

    def test_channel_not_found_is_reported_in_html(self) -> None:
        """A handle that can't be resolved should appear in the skipped-channel note."""
        digest = _make_digest()
        digest.summarize_descriptions_batch = MagicMock()
        digest.send_email = MagicMock()

        channels_yml = "channels:\n- '@missingchannel'\n"
        with (
            patch("builtins.open", mock_open(read_data=channels_yml)),
            patch("digest.resolve_channel", return_value=None) as resolve,
        ):
            digest.run()

        resolve.assert_called_once_with(
            digest.youtube, handle="@missingchannel", channel_id=None
        )
        html, _subject = digest.send_email.call_args.args
        assert "could not be checked" in html
        assert "@missingchannel" in html
        assert "channel not found" in html

    def test_error_fetching_videos_is_reported_and_does_not_abort_run(self) -> None:
        """One channel's fetch error should be reported but not stop other channels."""
        digest = _make_digest()
        digest.get_recent_videos = MagicMock(
            side_effect=[
                Exception("quota exceeded"),
                [_video_item("vid2", "Channel B")],
            ]
        )
        digest.get_video_details = MagicMock(
            return_value={"vid2": _video_detail("vid2", "Video Two")}
        )
        digest.summarize_descriptions_batch = MagicMock()
        digest.send_email = MagicMock()

        with patch("builtins.open", mock_open(read_data=CHANNELS_YML)):
            digest.run()

        html, subject = digest.send_email.call_args.args
        assert "error fetching videos" in html
        assert "(1 new video)" in subject

    def test_no_skipped_channels_omits_the_note(self) -> None:
        """When nothing was skipped, the warning note should not appear at all."""
        digest = _make_digest()
        digest.get_recent_videos = MagicMock(return_value=[])
        digest.summarize_descriptions_batch = MagicMock()
        digest.send_email = MagicMock()

        with patch("builtins.open", mock_open(read_data=CHANNELS_YML)):
            digest.run()

        html, _subject = digest.send_email.call_args.args
        assert "could not be checked" not in html


class TestRunBatchSummarizationWiring:
    """Tests confirming all channels' videos are summarized in one batch pass."""

    def test_all_channels_videos_summarized_in_single_batch_call(self) -> None:
        """Videos from every channel should be combined into one batch call."""
        digest = _make_digest()
        digest.get_recent_videos = MagicMock(
            side_effect=[
                [_video_item("vid1", "Channel A")],
                [_video_item("vid2", "Channel B")],
            ]
        )
        digest.get_video_details = MagicMock(
            side_effect=[
                {"vid1": _video_detail("vid1", "Video One")},
                {"vid2": _video_detail("vid2", "Video Two")},
            ]
        )
        digest.summarize_descriptions_batch = MagicMock()
        digest.send_email = MagicMock()

        with patch("builtins.open", mock_open(read_data=CHANNELS_YML)):
            digest.run()

        digest.summarize_descriptions_batch.assert_called_once()
        (all_videos_arg,) = digest.summarize_descriptions_batch.call_args.args
        assert set(all_videos_arg.keys()) == {"vid1", "vid2"}


class TestRunDigestFilter:
    """Tests for skipping channels that opted out with digest: false."""

    def test_digest_false_channel_is_not_fetched(self) -> None:
        """Only channels with digest: true (or no flag) should be fetched."""
        digest = _make_digest()
        digest.get_recent_videos = MagicMock(return_value=[])
        digest.summarize_descriptions_batch = MagicMock()
        digest.send_email = MagicMock()

        channels_yml = (
            "channels:\n"
            "- id: UC001\n  uploads_playlist_id: UU001\n  digest: false\n"
            "- id: UC002\n  uploads_playlist_id: UU002\n"
        )
        with patch("builtins.open", mock_open(read_data=channels_yml)):
            digest.run()

        digest.get_recent_videos.assert_called_once_with("UU002")


class TestRunStalePlaylistRecovery:
    """Tests for recovering from a stale/incorrect uploads playlist ID."""

    _STALE_YML = (
        "channels:\n"
        "- handle: '@WesRoth'\n"
        "  id: UCwrongwrongwrongwrong01\n"
        "  uploads_playlist_id: UUwrongwrongwrongwrong01\n"
    )

    _RESOLVED = ResolvedChannel(
        id="UCcorrectcorrectcorre01",
        handle="@WesRoth",
        title="Wes Roth",
        uploads_playlist_id="UUcorrectcorrectcorre01",
        strategy="handle",
    )

    def test_playlist_not_found_recovers_via_resolution(self) -> None:
        """A 404 playlistNotFound should re-resolve from the handle and stored ID."""
        digest = _make_digest()
        digest.get_recent_videos = MagicMock(
            side_effect=[
                _http_error(404, "playlistNotFound"),
                [_video_item("vid1", "Wes Roth")],
            ]
        )
        digest.get_video_details = MagicMock(
            return_value={"vid1": _video_detail("vid1", "Video One")}
        )
        digest.summarize_descriptions_batch = MagicMock()
        digest.send_email = MagicMock()

        with (
            patch("builtins.open", mock_open(read_data=self._STALE_YML)),
            patch("digest.resolve_channel", return_value=self._RESOLVED) as resolve,
        ):
            digest.run()

        resolve.assert_called_once_with(
            digest.youtube, handle="@WesRoth", channel_id="UCwrongwrongwrongwrong01"
        )
        digest.get_recent_videos.assert_called_with("UUcorrectcorrectcorre01")
        assert digest.get_recent_videos.call_count == 2
        html, subject = digest.send_email.call_args.args
        assert "Wes Roth" in html
        assert "(1 new video)" in subject

    def test_recovered_playlist_already_processed_is_skipped(self) -> None:
        """A recovered playlist that another entry already covered is not listed twice."""
        digest = _make_digest()
        digest.get_recent_videos = MagicMock(
            side_effect=[
                [_video_item("vid1", "Wes Roth")],
                _http_error(404, "playlistNotFound"),
                [_video_item("vid1", "Wes Roth")],
            ]
        )
        digest.get_video_details = MagicMock(
            return_value={"vid1": _video_detail("vid1", "Video One")}
        )
        digest.summarize_descriptions_batch = MagicMock()
        digest.send_email = MagicMock()

        channels_yml = (
            "channels:\n"
            "- handle: '@WesRoth'\n"
            "  uploads_playlist_id: UUcorrectcorrectcorre01\n"
        ) + self._STALE_YML.removeprefix("channels:\n")
        with (
            patch("builtins.open", mock_open(read_data=channels_yml)),
            patch("digest.resolve_channel", return_value=self._RESOLVED),
        ):
            digest.run()

        digest.get_video_details.assert_called_once()
        _html, subject = digest.send_email.call_args.args
        assert "(1 new video)" in subject

    def test_non_playlist_error_is_not_recovered(self) -> None:
        """A non-playlistNotFound error should skip the channel without re-resolving."""
        digest = _make_digest()
        digest.get_recent_videos = MagicMock(side_effect=_http_error(403, "forbidden"))
        digest.summarize_descriptions_batch = MagicMock()
        digest.send_email = MagicMock()

        with (
            patch("builtins.open", mock_open(read_data=self._STALE_YML)),
            patch("digest.resolve_channel") as resolve,
        ):
            digest.run()

        # No re-resolution should have been attempted, and the channel is skipped.
        resolve.assert_not_called()
        digest.get_recent_videos.assert_called_once()
        html, subject = digest.send_email.call_args.args
        assert "error fetching videos" in html
        assert "(no new videos)" in subject
