"""Tests for summary generation, truncation handling, and model fallback in digest.py."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import anthropic
import httpx2
from google.genai import errors, types

from digest import (
    CLAUDE_MODEL,
    CLAUDE_THINKING_TOKENS,
    CLAUDE_TIMEOUT_SECONDS,
    GEMINI_MAX_OUTPUT_TOKENS,
    GEMINI_MODEL,
    GEMINI_TIMEOUT_SECONDS,
    YouTubeDigest,
)

_CLAUDE_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _make_digest() -> YouTubeDigest:
    """Builds a YouTubeDigest instance without hitting any real APIs.

    Injects MagicMock Claude and Gemini clients so tests can control
    messages.create and generate_content directly.
    """
    return YouTubeDigest(
        youtube=MagicMock(), genai_client=MagicMock(), anthropic_client=MagicMock()
    )


def _claude_response(text: str, stop_reason: str = "end_turn") -> MagicMock:
    """Builds a fake Claude Message: an empty thinking block, then the text."""
    thinking = MagicMock(type="thinking", thinking="")
    block = MagicMock(type="text", text=text)
    return MagicMock(content=[thinking, block], stop_reason=stop_reason)


def _claude_error(status: int = 529) -> anthropic.APIStatusError:
    """Builds a Claude API error with the given HTTP status (529 is overloaded)."""
    response = httpx2.Response(status, request=_CLAUDE_REQUEST)
    return anthropic.APIStatusError("error", response=response, body=None)


def _quota_error(period: str) -> errors.ClientError:
    """Builds a Gemini 429 like the free-tier quota errors, for PerDay or PerMinute."""
    return errors.ClientError(
        429,
        {
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {
                        "violations": [
                            {
                                "quotaId": f"GenerateRequests{period}"
                                "PerProjectPerModel-FreeTier"
                            }
                        ]
                    }
                ],
            }
        },
    )


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
        digest.anthropic_client.messages.create.return_value = _claude_response(
            complete_summary
        )

        with patch("time.sleep"):
            result = digest.summarize_description(
                "Title: Phone Launch\nDescription: ..."
            )

        assert result == complete_summary
        assert digest.anthropic_client.messages.create.call_count == 1
        digest.genai_client.models.generate_content.assert_not_called()

    def test_empty_response_returns_placeholder(self) -> None:
        """An empty response should return the existing placeholder message."""
        digest = _make_digest()
        digest.anthropic_client.messages.create.return_value = _claude_response("")

        with patch("time.sleep"):
            result = digest.summarize_description("Title: X\nDescription: Y")

        assert result == "Summary unavailable: the model returned an empty response."


class TestSummarizeDescriptionTruncationRetry:
    """Tests for the max_tokens retry-then-trim behavior."""

    def test_truncated_response_triggers_retry_with_larger_budget(self) -> None:
        """A max_tokens response should trigger exactly one retry call with a
        larger max_tokens, and the retry's complete text should win."""
        digest = _make_digest()
        truncated = "This video explains the new update. It changes how the"
        complete = "This video explains the new update. It changes how the app handles notifications."
        create = digest.anthropic_client.messages.create
        create.side_effect = [
            _claude_response(truncated, stop_reason="max_tokens"),
            _claude_response(complete),
        ]

        with patch("time.sleep"):
            result = digest.summarize_description("Title: X\nDescription: Y")

        assert result == complete
        assert create.call_count == 2
        first_call, retry_call = create.call_args_list
        assert retry_call.kwargs["max_tokens"] > first_call.kwargs["max_tokens"]

    def test_truncated_gemini_fallback_retries_with_larger_budget(self) -> None:
        """A MAX_TOKENS reply from the Gemini fallback also gets a larger budget."""
        digest = _make_digest()
        digest.anthropic_client.messages.create.side_effect = _claude_error()
        generate = digest.genai_client.models.generate_content
        generate.side_effect = [
            _make_response("It changes how the", finish_reason="MAX_TOKENS"),
            _make_response("It changes how the app works.", finish_reason="STOP"),
        ]

        with patch("time.sleep"):
            result = digest.summarize_description("Title: X\nDescription: Y")

        assert result == "It changes how the app works."
        first_call, retry_call = generate.call_args_list
        assert retry_call.kwargs["config"].max_output_tokens == (
            first_call.kwargs["config"].max_output_tokens * 2
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

        digest.anthropic_client.messages.create.side_effect = [
            _claude_response(truncated, stop_reason="max_tokens"),
            _claude_response(still_truncated, stop_reason="max_tokens"),
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
        digest.anthropic_client.messages.create.side_effect = [
            Exception("network error"),
            _claude_response("Recovered summary after retry."),
        ]

        with patch("time.sleep"):
            result = digest.summarize_description("Title: X\nDescription: Y")

        assert result == "Recovered summary after retry."

    def test_exception_then_failed_retry_returns_placeholder(self) -> None:
        """If both the initial call and the retry raise, return the fallback message."""
        digest = _make_digest()
        digest.anthropic_client.messages.create.side_effect = [
            Exception("network error"),
            Exception("still down"),
        ]

        with patch("time.sleep"):
            result = digest.summarize_description("Title: X\nDescription: Y")

        assert result == "Summary unavailable: unexpected error (Exception)."

    def test_rate_limit_is_not_retried_and_names_the_limit(self) -> None:
        """A Gemini 429 after a Claude failure skips the 5s retry and names the limit."""
        digest = _make_digest()
        digest.anthropic_client.messages.create.side_effect = _claude_error()
        digest.genai_client.models.generate_content.side_effect = _quota_error(
            "PerMinute"
        )

        with patch("time.sleep") as mock_sleep:
            result = digest.summarize_description("Title: X\nDescription: Y")

        assert result == (
            "Summary unavailable: the Gemini free-tier per-minute request limit was hit."
        )
        # One call per model, and no second attempt.
        assert digest.anthropic_client.messages.create.call_count == 1
        assert digest.genai_client.models.generate_content.call_count == 1
        assert 5 not in [c.args[0] for c in mock_sleep.call_args_list]

    def test_daily_quota_stops_calls_to_gemini(self) -> None:
        """After a per-day 429, Gemini is not called again this run.

        The later video names Claude's failure, the one that left it without a
        summary.
        """
        digest = _make_digest()
        digest.anthropic_client.messages.create.side_effect = _claude_error()
        digest.genai_client.models.generate_content.side_effect = _quota_error("PerDay")

        with patch("time.sleep"):
            first = digest.summarize_description("Title: X\nDescription: Y")
            second = digest.summarize_description("Title: Z\nDescription: W")

        assert first == (
            "Summary unavailable: the Gemini free-tier daily request limit is used up."
        )
        assert second == "Summary unavailable: Claude was overloaded (529)."
        assert digest.genai_client.models.generate_content.call_count == 1


class TestWasTruncatedHelper:
    """Direct tests for the _was_truncated helper (Gemini responses)."""

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


class TestModelFallback:
    """Tests for falling back from CLAUDE_MODEL to GEMINI_MODEL."""

    def test_primary_success_uses_only_claude_at_high_effort(self) -> None:
        """A working Claude should be the only model called, at high effort."""
        digest = _make_digest()
        digest.anthropic_client.messages.create.return_value = _claude_response(
            "A summary."
        )

        with patch("time.sleep"):
            assert digest.summarize_description("Title: x") == "A summary."

        kwargs = digest.anthropic_client.messages.create.call_args.kwargs
        assert kwargs["model"] == CLAUDE_MODEL == "claude-haiku-5-5"
        assert kwargs["output_config"] == {"effort": "high"}
        assert kwargs["max_tokens"] == GEMINI_MAX_OUTPUT_TOKENS + CLAUDE_THINKING_TOKENS
        assert "temperature" not in kwargs
        digest.genai_client.models.generate_content.assert_not_called()
        assert digest.last_model == CLAUDE_MODEL

    def test_api_error_falls_back_to_gemini(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A Claude API error should retry on Gemini and record the takeover."""
        digest = _make_digest()
        digest.anthropic_client.messages.create.side_effect = _claude_error()
        digest.genai_client.models.generate_content.return_value = _make_response(
            "Fallback summary."
        )

        with patch("time.sleep"):
            assert digest.summarize_description("Title: x") == "Fallback summary."

        calls = digest.genai_client.models.generate_content.call_args_list
        assert [c.kwargs["model"] for c in calls] == [GEMINI_MODEL]
        assert f"{GEMINI_MODEL} answered." in capsys.readouterr().out
        assert digest.retries == {
            f"{GEMINI_MODEL} took over after {CLAUDE_MODEL} failed: "
            "Claude was overloaded (529)": 1
        }

    def test_timeout_falls_back_to_gemini(self) -> None:
        """A Claude request that times out should retry on Gemini."""
        digest = _make_digest()
        digest.anthropic_client.messages.create.side_effect = anthropic.APITimeoutError(
            request=_CLAUDE_REQUEST
        )
        digest.genai_client.models.generate_content.return_value = _make_response(
            "Fallback summary."
        )

        with patch("time.sleep"):
            assert digest.summarize_description("Title: x") == "Fallback summary."

        assert digest.genai_client.models.generate_content.call_count == 1
        assert f"Claude did not answer within {CLAUDE_TIMEOUT_SECONDS} seconds" in next(
            iter(digest.retries)
        )

    def test_refusal_falls_back_to_gemini(self) -> None:
        """A Claude refusal should retry on Gemini instead of losing the summary."""
        digest = _make_digest()
        digest.anthropic_client.messages.create.return_value = _claude_response(
            "", stop_reason="refusal"
        )
        digest.genai_client.models.generate_content.return_value = _make_response(
            "Fallback summary."
        )

        with patch("time.sleep"):
            assert digest.summarize_description("Title: x") == "Fallback summary."

        assert "Claude declined to summarize it" in next(iter(digest.retries))
        assert digest.last_model == GEMINI_MODEL

    def test_clients_have_request_timeouts(self) -> None:
        """The default clients use their timeouts, and Claude has no SDK retries."""
        with (
            patch("digest.googleapiclient.discovery.build"),
            patch("digest.environment.youtube_api_key", return_value="k"),
            patch("digest.environment.gemini_api_key", return_value="k"),
            patch("digest.environment.anthropic_api_key", return_value="a"),
            patch("digest.genai.Client") as gemini_client,
            patch("digest.anthropic.Anthropic") as claude_client,
        ):
            YouTubeDigest()

        http_options = gemini_client.call_args.kwargs["http_options"]
        assert http_options.timeout == GEMINI_TIMEOUT_SECONDS * 1000
        claude_client.assert_called_once_with(
            api_key="a", timeout=CLAUDE_TIMEOUT_SECONDS, max_retries=0
        )

    def test_batch_uses_fallback_model(self) -> None:
        """The batch path should also fall back, and ask Gemini for JSON."""
        digest = _make_digest()
        digest.anthropic_client.messages.create.side_effect = _claude_error()
        digest.genai_client.models.generate_content.return_value = _make_response(
            '{"v1": "Batch summary."}'
        )
        batch = {"v1": MagicMock(title="T", description="D")}

        with patch("time.sleep"):
            assert digest._summarize_batch_via_gemini(batch) == {"v1": "Batch summary."}

        call = digest.genai_client.models.generate_content.call_args
        assert call.kwargs["model"] == GEMINI_MODEL
        assert call.kwargs["config"].response_mime_type == "application/json"

    def test_non_api_error_does_not_use_fallback(self) -> None:
        """A non-API error (e.g. a bug) should not try the fallback model."""
        digest = _make_digest()
        digest.anthropic_client.messages.create.side_effect = Exception("boom")

        with patch("time.sleep"):
            assert digest.summarize_description("Title: x") == (
                "Summary unavailable: unexpected error (Exception)."
            )

        digest.genai_client.models.generate_content.assert_not_called()
