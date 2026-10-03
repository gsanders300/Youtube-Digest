"""Tests for batched Gemini summarization."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from digest import (
    GEMINI_BATCH_SIZE,
    VideoDetail,
    YouTubeDigest,
)


def _make_digest() -> YouTubeDigest:
    """Builds a YouTubeDigest instance without hitting any real APIs.

    Injects MagicMocks for both the YouTube client and the Gemini client.
    """
    return YouTubeDigest(youtube=MagicMock(), genai_client=MagicMock())


def _make_gemini_response(text: str, finish_reason: str = "STOP") -> MagicMock:
    """Builds a fake Gemini GenerateContentResponse for mocking."""
    response = MagicMock()
    response.text = text
    candidate = MagicMock()
    candidate.finish_reason = finish_reason
    response.candidates = [candidate]
    return response


def _make_videos(count: int) -> dict[str, VideoDetail]:
    """Builds a dict of fake VideoDetail objects keyed by video ID."""
    return {
        f"vid{i}": VideoDetail(
            title=f"Title {i}",
            description=f"Description {i}",
            duration="5:00",
            link=f"https://www.youtube.com/watch?v=vid{i}",
        )
        for i in range(count)
    }


class TestSummarizeDescriptionsBatchOrchestration:
    """Tests for the batch/fallback orchestration logic."""

    def test_all_summaries_present_uses_batch_result_only(self) -> None:
        """If the batch call returns every summary, no fallback should occur."""
        digest = _make_digest()
        videos = _make_videos(3)

        with patch.object(
            YouTubeDigest,
            "_summarize_batch_via_gemini",
            return_value={vid: f"Summary for {vid}." for vid in videos},
        ) as mock_batch:
            with patch.object(YouTubeDigest, "summarize_description") as mock_single:
                digest.summarize_descriptions_batch(videos)

        mock_batch.assert_called_once()
        mock_single.assert_not_called()
        for vid, detail in videos.items():
            assert detail.summary == f"Summary for {vid}."

    def test_missing_video_in_batch_result_falls_back_to_single(self) -> None:
        """A video missing from the batch result should get a per-video summary."""
        digest = _make_digest()
        videos = _make_videos(2)
        vid_ids = list(videos.keys())

        with patch.object(
            YouTubeDigest,
            "_summarize_batch_via_gemini",
            return_value={vid_ids[0]: "Batch summary."},
        ):
            with patch.object(
                YouTubeDigest,
                "summarize_description",
                return_value="Fallback summary.",
            ) as mock_single:
                digest.summarize_descriptions_batch(videos)

        assert videos[vid_ids[0]].summary == "Batch summary."
        assert videos[vid_ids[1]].summary == "Fallback summary."
        mock_single.assert_called_once()

    def test_videos_are_chunked_by_batch_size(self) -> None:
        """More videos than GEMINI_BATCH_SIZE should trigger multiple batch calls."""
        digest = _make_digest()
        videos = _make_videos(GEMINI_BATCH_SIZE + 1)

        with patch.object(
            YouTubeDigest, "_summarize_batch_via_gemini", return_value={}
        ) as mock_batch:
            with patch.object(
                YouTubeDigest, "summarize_description", return_value="Fallback."
            ):
                digest.summarize_descriptions_batch(videos)

        # One extra video beyond the batch size requires a second chunk call.
        assert mock_batch.call_count == 2
        first_chunk_size = len(mock_batch.call_args_list[0].args[0])
        second_chunk_size = len(mock_batch.call_args_list[1].args[0])
        assert first_chunk_size == GEMINI_BATCH_SIZE
        assert second_chunk_size == 1


class TestSummarizeBatchViaGemini:
    """Tests for the raw Gemini batch-call + JSON parsing logic."""

    def test_valid_json_response_returns_all_summaries(self) -> None:
        """A well-formed JSON response should be parsed into a summary dict."""
        digest = _make_digest()
        batch = _make_videos(2)
        digest.genai_client.models.generate_content.return_value = (
            _make_gemini_response('{"vid0": "Summary zero.", "vid1": "Summary one."}')
        )

        with patch("time.sleep"):
            result = digest._summarize_batch_via_gemini(batch)

        assert result == {"vid0": "Summary zero.", "vid1": "Summary one."}

    def test_json_wrapped_in_code_fences_is_parsed(self) -> None:
        """A response wrapped in markdown code fences should still parse."""
        digest = _make_digest()
        batch = _make_videos(1)
        digest.genai_client.models.generate_content.return_value = (
            _make_gemini_response('```json\n{"vid0": "Summary zero."}\n```')
        )

        with patch("time.sleep"):
            result = digest._summarize_batch_via_gemini(batch)

        assert result == {"vid0": "Summary zero."}

    def test_invalid_json_returns_empty_dict(self) -> None:
        """Non-JSON text should result in an empty dict (triggers fallback)."""
        digest = _make_digest()
        batch = _make_videos(1)
        digest.genai_client.models.generate_content.return_value = (
            _make_gemini_response("this is not json", finish_reason="STOP")
        )

        with patch("time.sleep"):
            result = digest._summarize_batch_via_gemini(batch)

        assert result == {}

    def test_truncated_invalid_json_returns_empty_dict(self) -> None:
        """A MAX_TOKENS-truncated (and therefore invalid) response returns {}."""
        digest = _make_digest()
        batch = _make_videos(1)
        digest.genai_client.models.generate_content.return_value = (
            _make_gemini_response(
                '{"vid0": "Summary that got cut off mid', finish_reason="MAX_TOKENS"
            )
        )

        with patch("time.sleep"):
            result = digest._summarize_batch_via_gemini(batch)

        assert result == {}

    def test_non_dict_json_returns_empty_dict(self) -> None:
        """Valid JSON that isn't an object (e.g. a list) should be rejected."""
        digest = _make_digest()
        batch = _make_videos(1)
        digest.genai_client.models.generate_content.return_value = (
            _make_gemini_response('["not", "a", "dict"]')
        )

        with patch("time.sleep"):
            result = digest._summarize_batch_via_gemini(batch)

        assert result == {}

    def test_empty_response_returns_empty_dict(self) -> None:
        """An empty response should return {} rather than raise."""
        digest = _make_digest()
        batch = _make_videos(1)
        digest.genai_client.models.generate_content.return_value = (
            _make_gemini_response("")
        )

        with patch("time.sleep"):
            result = digest._summarize_batch_via_gemini(batch)

        assert result == {}

    def test_exception_returns_empty_dict(self) -> None:
        """An API exception should be caught and return {} rather than raise."""
        digest = _make_digest()
        batch = _make_videos(1)
        digest.genai_client.models.generate_content.side_effect = Exception("boom")

        with patch("time.sleep"):
            result = digest._summarize_batch_via_gemini(batch)

        assert result == {}


class TestStripJsonFences:
    """Direct tests for the _strip_json_fences helper."""

    def test_strips_json_language_fence(self) -> None:
        text = '```json\n{"a": 1}\n```'
        assert YouTubeDigest._strip_json_fences(text) == '{"a": 1}'

    def test_strips_plain_fence(self) -> None:
        text = '```\n{"a": 1}\n```'
        assert YouTubeDigest._strip_json_fences(text) == '{"a": 1}'

    def test_leaves_unfenced_text_unchanged(self) -> None:
        text = '{"a": 1}'
        assert YouTubeDigest._strip_json_fences(text) == '{"a": 1}'
