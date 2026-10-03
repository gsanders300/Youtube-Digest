"""Tests for Gemini summary generation and truncation handling in digest.py."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from google.genai import types

from digest import YouTubeDigest


def _make_digest() -> YouTubeDigest:
    """Builds a YouTubeDigest instance without hitting any real APIs.

    Injects a MagicMock Gemini client so tests can control generate_content's
    return value directly.
    """
    return YouTubeDigest(youtube=MagicMock(), genai_client=MagicMock())


def _make_response(text: str, finish_reason: str = "STOP") -> MagicMock:
    """Builds a fake Gemini GenerateContentResponse for mocking."""
    response = MagicMock()
    response.text = text
    candidate = MagicMock()
    candidate.finish_reason = finish_reason
    response.candidates = [candidate]
    return response


class TestSummarizeDescriptionHappyPath:
    """Tests for the normal, non-truncated summary path."""

    def test_complete_response_passes_through_unchanged(self) -> None:
        """A complete response should be returned as-is, with no retry."""
        digest = _make_digest()
        complete_summary = "This video covers a new phone launch. It highlights the camera and battery upgrades."
        digest.genai_client.models.generate_content.return_value = _make_response(
            complete_summary, finish_reason="STOP"
        )

        with patch("time.sleep"):
            result = digest.summarize_description(
                "Title: Phone Launch\nDescription: ..."
            )

        assert result == complete_summary
        assert digest.genai_client.models.generate_content.call_count == 1

    def test_empty_response_returns_placeholder(self) -> None:
        """An empty response should return the existing placeholder message."""
        digest = _make_digest()
        digest.genai_client.models.generate_content.return_value = _make_response("")

        with patch("time.sleep"):
            result = digest.summarize_description("Title: X\nDescription: Y")

        assert result == "Summary unavailable (empty response)."


class TestSummarizeDescriptionTruncationRetry:
    """Tests for the MAX_TOKENS retry-then-trim behavior."""

    def test_truncated_response_triggers_retry_with_larger_budget(self) -> None:
        """A MAX_TOKENS response should trigger exactly one retry call with a
        doubled max_output_tokens, and the retry's complete text should win."""
        digest = _make_digest()
        truncated = "This video explains the new update. It changes how the"
        complete = "This video explains the new update. It changes how the app handles notifications."

        truncated_response = _make_response(truncated, finish_reason="MAX_TOKENS")
        complete_response = _make_response(complete, finish_reason="STOP")
        digest.genai_client.models.generate_content.side_effect = [
            truncated_response,
            complete_response,
        ]

        with patch("time.sleep"):
            result = digest.summarize_description("Title: X\nDescription: Y")

        assert result == complete
        assert digest.genai_client.models.generate_content.call_count == 2

        # The retry call should have used a larger max_output_tokens than the
        # initial call, and should have kept thinking disabled.
        first_call_config = digest.genai_client.models.generate_content.call_args_list[
            0
        ].kwargs["config"]
        retry_call_config = digest.genai_client.models.generate_content.call_args_list[
            1
        ].kwargs["config"]
        assert retry_call_config.max_output_tokens > first_call_config.max_output_tokens
        # The retry should preserve the initial call's minimal-thinking setting.
        assert retry_call_config.thinking_config == first_call_config.thinking_config
        assert (
            retry_call_config.thinking_config.thinking_level
            == first_call_config.thinking_config.thinking_level
        )

    def test_still_truncated_after_retry_is_trimmed_to_last_sentence(self) -> None:
        """If the retry is also truncated, the result must be trimmed back to
        the last complete sentence rather than emailed as a hanging fragment."""
        digest = _make_digest()
        truncated = (
            "This video explains the launch. It also covers pricing details and how"
        )
        still_truncated = (
            "This video explains the launch. It also covers pricing details and how "
            "the rollout will proceed across regions over the next few"
        )

        digest.genai_client.models.generate_content.side_effect = [
            _make_response(truncated, finish_reason="MAX_TOKENS"),
            _make_response(still_truncated, finish_reason="MAX_TOKENS"),
        ]

        with patch("time.sleep"):
            result = digest.summarize_description("Title: X\nDescription: Y")

        # Trimmed back to the last complete sentence; the hanging "over the
        # next few" fragment must not appear in the emailed summary.
        assert result == "This video explains the launch."
        assert result.endswith((".", "!", "?"))


class TestSummarizeDescriptionExceptionHandling:
    """Regression tests for the existing exception-based retry path."""

    def test_exception_then_successful_retry(self) -> None:
        """If the first call raises, the existing 5s-delay retry should still work."""
        digest = _make_digest()
        digest.genai_client.models.generate_content.side_effect = [
            Exception("network error"),
            _make_response("Recovered summary after retry.", finish_reason="STOP"),
        ]

        with patch("time.sleep"):
            result = digest.summarize_description("Title: X\nDescription: Y")

        assert result == "Recovered summary after retry."

    def test_exception_then_failed_retry_returns_placeholder(self) -> None:
        """If both the initial call and the retry raise, return the fallback message."""
        digest = _make_digest()
        digest.genai_client.models.generate_content.side_effect = [
            Exception("network error"),
            Exception("still down"),
        ]

        with patch("time.sleep"):
            result = digest.summarize_description("Title: X\nDescription: Y")

        assert result == "Summary unavailable."


class TestWasTruncatedHelper:
    """Direct tests for the _was_truncated helper."""

    def test_returns_true_for_max_tokens(self) -> None:
        response = _make_response("text", finish_reason=types.FinishReason.MAX_TOKENS)
        assert YouTubeDigest._was_truncated(response) is True

    def test_returns_false_for_stop(self) -> None:
        response = _make_response("text", finish_reason=types.FinishReason.STOP)
        assert YouTubeDigest._was_truncated(response) is False

    def test_returns_false_when_no_candidates(self) -> None:
        response = MagicMock()
        response.candidates = []
        assert YouTubeDigest._was_truncated(response) is False


class TestTrimToLastCompleteSentence:
    """Direct tests for the _trim_to_last_complete_sentence helper."""

    def test_trims_hanging_fragment(self) -> None:
        text = "First sentence is complete. Second sentence is cut off mid"
        assert (
            YouTubeDigest._trim_to_last_complete_sentence(text)
            == "First sentence is complete."
        )

    def test_leaves_complete_text_unchanged(self) -> None:
        text = "First sentence. Second sentence!"
        assert YouTubeDigest._trim_to_last_complete_sentence(text) == text

    def test_returns_original_when_no_terminator_found(self) -> None:
        text = "No terminal punctuation at all"
        assert YouTubeDigest._trim_to_last_complete_sentence(text) == text
