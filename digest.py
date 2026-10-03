from __future__ import annotations
import datetime
import json
import random
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from typing import Any

import googleapiclient.discovery
import isodate
from zoneinfo import ZoneInfo
from google import genai
from google.genai import types
from pydantic import BaseModel

import channel_list
import environment
from youtube_api import execute, is_playlist_not_found, resolve_channel

# --- Gemini generation tuning -----------------------------------------------
# Centralized here so tuning affects both the batched and single-video
# summarization paths consistently.
GEMINI_MODEL = "gemini-3.1-flash-lite"
GEMINI_TEMPERATURE = 0.7
GEMINI_MAX_OUTPUT_TOKENS = 1024
GEMINI_BATCH_MAX_OUTPUT_TOKENS = 4096
GEMINI_BATCH_SIZE = 12  # Videos summarized per batched Gemini call.
DESCRIPTION_CHAR_LIMIT = 6000  # Max description chars sent to Gemini per video.

# How far back to look for "new" videos.
LOOKBACK_HOURS = 24


class VideoDetail(BaseModel):
    """Details for a specific YouTube video."""

    title: str
    description: str
    duration: str
    link: str
    summary: str | None = None
    published_at: datetime.datetime | None = None


def _publish_sort_key(video: VideoDetail) -> datetime.datetime:
    """Returns a timezone-aware sort key for ascending chronological ordering.

    Videos without a known publish time sort to the top (oldest position) by
    falling back to the minimum representable UTC datetime, which also keeps
    the key type uniform so naive and aware values never get compared.

    Args:
        video: The video whose publish time is used as the sort key.

    Returns:
        The video's publish time, or a minimum UTC datetime if it is unknown.
    """
    if video.published_at is None:
        return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    published_at = video.published_at
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=datetime.timezone.utc)
    return published_at


class YouTubeDigest:
    """Service for generating and sending a daily YouTube video digest."""

    def __init__(self, youtube: Any = None, genai_client: Any = None) -> None:
        """Initializes the YouTubeDigest service with API clients.

        Args:
            youtube: A YouTube Data API v3 client; built from YOUTUBE_API_KEY if None.
            genai_client: A Gemini client; built from GEMINI_API_KEY if None.
        """
        self.youtube = youtube or googleapiclient.discovery.build(
            "youtube", "v3", developerKey=environment.youtube_api_key()
        )
        self.genai_client = genai_client or genai.Client(
            api_key=environment.gemini_api_key()
        )

    def get_recent_videos(self, playlist_id: str) -> list[dict[str, Any]]:
        """Fetches videos published within the lookback window from a playlist."""
        tz_eastern = ZoneInfo("America/New_York")
        cutoff = datetime.datetime.now(tz_eastern) - datetime.timedelta(
            hours=LOOKBACK_HOURS
        )
        request = self.youtube.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=playlist_id,
            maxResults=20,
        )
        response = execute(request)
        videos = []
        for item in response.get("items", []):
            pub_date_str = (
                item["contentDetails"].get("videoPublishedAt")
                or item["snippet"]["publishedAt"]
            )
            pub_date = isodate.parse_datetime(pub_date_str)

            # Ensure pub_date is compared in Eastern time
            if pub_date.tzinfo is None:
                pub_date = pub_date.replace(tzinfo=datetime.timezone.utc)
            pub_date_eastern = pub_date.astimezone(tz_eastern)

            if pub_date_eastern >= cutoff:
                videos.append(item)
        return videos

    def _get_recent_videos_with_fallback(
        self, channel: channel_list.Channel, uploads_playlist_id: str
    ) -> tuple[str, list[dict[str, Any]]]:
        """Fetches recent videos, recovering from a stale uploads playlist ID.

        A channels.yml entry can carry an incorrect channel ID (for example one
        added by hand), which produces an uploads playlist ID that YouTube
        reports as not found. When that happens, the channel is re-resolved from
        its @handle, then its stored ID, and the fetch is retried once, so a
        wrong stored ID degrades to a slower lookup instead of silently dropping
        the channel from the digest.

        Args:
            channel: The channels.yml entry being fetched.
            uploads_playlist_id: The uploads playlist ID to try first.

        Returns:
            A (playlist_id, videos) tuple, where playlist_id is the uploads
            playlist the videos actually came from.

        Raises:
            Exception: If the fetch fails for any reason other than a
                recoverable playlistNotFound, or if re-resolution also fails.
        """
        try:
            return uploads_playlist_id, self.get_recent_videos(uploads_playlist_id)
        except Exception as error:
            if not is_playlist_not_found(error):
                raise
            label = channel.handle or channel.id
            print(
                f"  Uploads playlist {uploads_playlist_id} not found for {label}; "
                "re-resolving..."
            )
            resolved = resolve_channel(
                self.youtube, handle=channel.handle, channel_id=channel.id
            )
            if not resolved or resolved.uploads_playlist_id == uploads_playlist_id:
                raise
            recovered = resolved.uploads_playlist_id
            print(
                f"  Re-resolved {label} (via {resolved.strategy}) to uploads "
                f"playlist {recovered}; retrying."
            )
            return recovered, self.get_recent_videos(recovered)

    def get_video_details(self, video_ids: list[str]) -> dict[str, VideoDetail]:
        """Fetches title, duration, and description for a list of video IDs."""
        if not video_ids:
            return {}
        request = self.youtube.videos().list(
            part="snippet,contentDetails", id=",".join(video_ids)
        )
        response = execute(request)
        details = {}
        for item in response.get("items", []):
            raw_duration = item.get("contentDetails", {}).get("duration")
            if not raw_duration:
                # Skip live streams and scheduled premieres, which have no duration.
                continue
            duration = isodate.parse_duration(raw_duration)
            total_seconds = int(duration.total_seconds())
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 0:
                duration_str = f"{hours}:{minutes:02}:{seconds:02}"
            else:
                duration_str = f"{minutes}:{seconds:02}"

            raw_published = item.get("snippet", {}).get("publishedAt")
            published_at = (
                isodate.parse_datetime(raw_published) if raw_published else None
            )

            details[item["id"]] = VideoDetail(
                title=item["snippet"]["title"],
                description=item["snippet"]["description"],
                duration=duration_str,
                link=f"https://www.youtube.com/watch?v={item['id']}",
                published_at=published_at,
            )
        return details

    def summarize_description(self, text_to_summarize: str) -> str:
        """Generates a 2-3 sentence summary of the video content using Gemini.

        Disables Gemini's "thinking" mode so the full max_output_tokens budget
        is spent on the visible summary rather than internal reasoning tokens,
        and repairs any response that is still cut off mid-sentence by retrying
        once with a larger budget and, failing that, trimming back to the last
        complete sentence.

        Args:
            text_to_summarize: The video title and description to summarize.

        Returns:
            A summary string that always ends on a complete sentence.
        """
        # Add a small delay between requests
        time.sleep(1)

        prompt = (
            "Summarize this YouTube video content based on its title and description. "
            "Write two to three clear, complete, and grammatically correct sentences that "
            "capture the core content in a bit more detail than a one-line blurb. "
            "DO NOT leave sentences unfinished or cut off mid-sentence. Ensure the final sentence ends with a period. "
            "Avoid clickbait or fluff. Output ONLY the summary text.\n\n"
            f"{text_to_summarize[:DESCRIPTION_CHAR_LIMIT]}"
        )

        # Define generation config inside the call or as a separate object
        gen_config = types.GenerateContentConfig(
            max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS,
            temperature=GEMINI_TEMPERATURE,
            thinking_config=types.ThinkingConfig(thinking_level="minimal"),
        )

        try:
            # We move the system instruction into the prompt or the config if supported by this SDK version
            # Using a slightly safer approach for the prompt structure
            response = self.genai_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=gen_config,
            )

            if not response or not response.text:
                print("Empty response from Gemini")
                return "Summary unavailable (empty response)."

            if self._was_truncated(response):
                print(
                    "  Warning: summary was truncated (MAX_TOKENS); retrying with a "
                    "larger token budget..."
                )
                retry_config = types.GenerateContentConfig(
                    max_output_tokens=gen_config.max_output_tokens * 2,
                    temperature=gen_config.temperature,
                    thinking_config=gen_config.thinking_config,
                )
                retry_response = self.genai_client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt,
                    config=retry_config,
                )
                if retry_response and retry_response.text:
                    response = retry_response

            summary = response.text.strip()

            if self._was_truncated(response):
                print(
                    "  Warning: summary still truncated after retry; trimming to "
                    "the last complete sentence."
                )
                summary = self._trim_to_last_complete_sentence(summary)

            return summary

        except Exception as e:
            print(f"Error generating summary: {e}")

            # Simple retry with a longer wait
            try:
                print("Retrying with 5s delay...")
                time.sleep(5)
                response = self.genai_client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt,
                    config=gen_config,
                )
                return (
                    response.text.strip()
                    if response and response.text
                    else "Summary unavailable."
                )
            except Exception as retry_e:
                print(f"Retry failed: {retry_e}")
                return "Summary unavailable."

    @staticmethod
    def _was_truncated(response: types.GenerateContentResponse) -> bool:
        """Checks whether a Gemini response was cut off by the token limit.

        Args:
            response: The response returned by the Gemini API.

        Returns:
            True if the first candidate's generation stopped because it hit
            max_output_tokens, False otherwise.
        """
        candidates = getattr(response, "candidates", None)
        if not candidates:
            return False
        return candidates[0].finish_reason == types.FinishReason.MAX_TOKENS

    @staticmethod
    def _trim_to_last_complete_sentence(text: str) -> str:
        """Trims text back to the last sentence-ending punctuation mark.

        Acts as a last-resort safeguard so a summary that is still truncated
        after a retry is never emailed as a hanging mid-sentence fragment.

        Args:
            text: The (possibly truncated) summary text.

        Returns:
            The text trimmed to end after the last '.', '!', or '?'. If no
            sentence-ending punctuation is found, the original text is
            returned unchanged.
        """
        last_terminator = max(text.rfind("."), text.rfind("!"), text.rfind("?"))
        if last_terminator == -1:
            return text
        return text[: last_terminator + 1].strip()

    def summarize_descriptions_batch(self, videos: dict[str, VideoDetail]) -> None:
        """Summarizes many videos using as few Gemini calls as possible.

        Videos are grouped into chunks of GEMINI_BATCH_SIZE and each chunk is
        summarized with a single Gemini request that returns a JSON object
        mapping video ID to summary, instead of one request per video. Any
        video whose summary could not be parsed out of a chunk's response
        (e.g. truncation or malformed JSON) falls back to the slower but more
        robust single-video summarize_description(), so no video is ever left
        without a summary.

        Args:
            videos: Mapping of video ID to its VideoDetail. Each detail's
                `summary` field is populated in place.
        """
        video_ids = list(videos.keys())
        for start in range(0, len(video_ids), GEMINI_BATCH_SIZE):
            batch_ids = video_ids[start : start + GEMINI_BATCH_SIZE]
            batch = {vid: videos[vid] for vid in batch_ids}
            summaries = self._summarize_batch_via_gemini(batch)

            for vid in batch_ids:
                summary = summaries.get(vid)
                if summary:
                    videos[vid].summary = summary
                else:
                    print(
                        f"  Falling back to single-video summary for: {videos[vid].title}"
                    )
                    context = (
                        f"Title: {videos[vid].title}\n"
                        f"Description: {videos[vid].description}"
                    )
                    videos[vid].summary = self.summarize_description(context)

    def _summarize_batch_via_gemini(
        self, batch: dict[str, VideoDetail]
    ) -> dict[str, str]:
        """Sends one Gemini request to summarize multiple videos at once.

        Args:
            batch: Mapping of video ID to VideoDetail for a single chunk.

        Returns:
            A mapping of video ID to summary for every video Gemini returned
            a usable entry for. Entries that are missing, invalid, or could
            not be parsed are simply absent so the caller can fall back to
            summarize_description() for just those videos.
        """
        # Add a small delay between requests
        time.sleep(1)

        videos_block = "\n\n".join(
            f'Video ID: "{vid}"\n'
            f"Title: {detail.title}\n"
            f"Description: {detail.description[:DESCRIPTION_CHAR_LIMIT]}"
            for vid, detail in batch.items()
        )

        prompt = (
            "Summarize each of the following YouTube videos based on its title and "
            "description. For EACH video, write two to three clear, complete, and "
            "grammatically correct sentences that capture the core content in a bit "
            "more detail than a one-line blurb. DO NOT leave sentences unfinished or "
            "cut off mid-sentence, and ensure the final sentence of each summary ends "
            "with a period. Avoid clickbait or fluff.\n\n"
            "Respond with ONLY a single valid JSON object mapping each Video ID to its "
            "summary string, with no markdown formatting, code fences, or extra "
            'commentary. Example shape: {"abc123": "Summary text.", "def456": '
            '"Summary text."}\n\n'
            f"{videos_block}"
        )

        gen_config = types.GenerateContentConfig(
            max_output_tokens=GEMINI_BATCH_MAX_OUTPUT_TOKENS,
            temperature=GEMINI_TEMPERATURE,
            thinking_config=types.ThinkingConfig(thinking_level="minimal"),
            response_mime_type="application/json",
        )

        response = None
        try:
            response = self.genai_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=gen_config,
            )

            if not response or not response.text:
                print("Empty response from Gemini for batch summary.")
                return {}

            raw_text = self._strip_json_fences(response.text)
            summaries = json.loads(raw_text)

            if not isinstance(summaries, dict):
                print(
                    "Batch summary response was not a JSON object; falling back "
                    "to per-video summaries for this batch."
                )
                return {}

            return {
                str(vid): str(summary).strip()
                for vid, summary in summaries.items()
                if summary
            }

        except json.JSONDecodeError as e:
            if response is not None and self._was_truncated(response):
                print(
                    f"Batch summary was truncated (MAX_TOKENS) and produced invalid "
                    f"JSON: {e}. Falling back to per-video summaries for this batch."
                )
            else:
                print(
                    f"Failed to parse batch summary JSON: {e}. Falling back to "
                    "per-video summaries for this batch."
                )
            return {}
        except Exception as e:
            print(
                f"Error generating batch summary: {e}. Falling back to per-video "
                "summaries for this batch."
            )
            return {}

    @staticmethod
    def _strip_json_fences(text: str) -> str:
        """Strips optional markdown code fences (e.g. ```json ... ```) from text.

        Args:
            text: The raw text returned by Gemini.

        Returns:
            The text with any leading/trailing code fence markers removed.
        """
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`").strip()
            if stripped.lower().startswith("json"):
                stripped = stripped[4:].strip()
        return stripped

    def send_email(self, html_content: str, subject: str) -> None:
        """Sends the HTML email via Gmail SMTP.

        Raises:
            RuntimeError: If required SMTP credentials or the recipient are
                missing. Failing loudly ensures the GitHub Actions run is
                marked as failed (and its alerting issue is opened) rather
                than silently exiting successfully with no email sent.
            Exception: Any error raised by smtplib while connecting, logging
                in, or sending is propagated for the same reason.
        """
        gmail_user = environment.gmail_user()
        recipient = environment.recipient_email()
        app_password = environment.gmail_app_password()

        if not all([gmail_user, recipient, app_password]):
            raise RuntimeError(
                "Cannot send email: one or more of GMAIL_USER, RECIPIENT_EMAIL, "
                "or GMAIL_APP_PASSWORD is missing from the environment."
            )

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = gmail_user
        msg["To"] = recipient

        msg.attach(MIMEText(html_content, "html"))

        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
                server.login(gmail_user, app_password)
                server.sendmail(gmail_user, recipient, msg.as_string())
            print("Email sent successfully.")
        except Exception as e:
            print(f"Failed to send email: {e}")
            raise

    def run(self) -> None:
        """Main execution logic for the digest service."""
        tz_eastern = ZoneInfo("America/New_York")
        start_time = datetime.datetime.now(tz_eastern)

        channels = channel_list.load()

        # Filter to channels opted into the digest.
        digest_channels = [channel for channel in channels if channel.digest]

        total_subscriptions = len(channels)
        total_included = len(digest_channels)
        print(
            f"Processing {total_included} of {total_subscriptions} channels (digest: true)"
        )

        # digest_data is keyed by the channel's uploads playlist ID (a stable,
        # unique identifier) rather than its display title, so two channels
        # that happen to share a title do not overwrite each other's videos.
        # channel_titles maps that key back to the title for the email heading.
        digest_data: dict[str, list[VideoDetail]] = {}
        channel_titles: dict[str, str] = {}
        processed_playlists: set[str] = set()
        all_videos: dict[str, VideoDetail] = {}
        skipped_channels: list[str] = []
        total_videos = 0

        for channel in digest_channels:
            handle = channel.handle or channel.id
            print(f"Processing {handle}...")
            uploads_playlist_id = channel.uploads_playlist_id

            if not uploads_playlist_id:
                try:
                    resolved = resolve_channel(
                        self.youtube, handle=channel.handle, channel_id=channel.id
                    )
                except Exception as e:
                    print(f"Error resolving channel {handle}: {e}")
                    skipped_channels.append(f"{handle}: error resolving channel")
                    continue
                if not resolved:
                    print(f"Could not resolve channel {handle}")
                    skipped_channels.append(f"{handle}: channel not found")
                    continue
                uploads_playlist_id = resolved.uploads_playlist_id

            if uploads_playlist_id in processed_playlists:
                # A duplicate entry in channels.yml. Skip it before spending
                # any quota so its videos are not fetched, counted, or listed
                # twice.
                print(f"Skipping duplicate channel entry for {handle}")
                continue
            processed_playlists.add(uploads_playlist_id)

            try:
                fetched_playlist_id, videos = self._get_recent_videos_with_fallback(
                    channel, uploads_playlist_id
                )
            except Exception as e:
                print(f"Error fetching videos for {handle}: {e}")
                skipped_channels.append(f"{handle}: error fetching videos")
                continue

            if fetched_playlist_id != uploads_playlist_id:
                # Recovery found the channel's real playlist; it may belong to
                # another entry that was already processed.
                if fetched_playlist_id in processed_playlists:
                    print(f"Skipping duplicate channel entry for {handle}")
                    continue
                processed_playlists.add(fetched_playlist_id)
                uploads_playlist_id = fetched_playlist_id

            if not videos:
                continue

            try:
                video_ids = [v["contentDetails"]["videoId"] for v in videos]
                details = self.get_video_details(video_ids)
            except Exception as e:
                print(f"Error fetching video details for {handle}: {e}")
                skipped_channels.append(f"{handle}: error fetching video details")
                continue

            if not details:
                # Every recent upload was a live stream or scheduled premiere
                # (no duration), so there is nothing to summarize. Skip the
                # channel entirely rather than emitting an empty heading.
                continue

            channel_name = videos[0]["snippet"]["channelTitle"]
            digest_data[uploads_playlist_id] = []
            channel_titles[uploads_playlist_id] = channel_name

            for v_id, detail in details.items():
                digest_data[uploads_playlist_id].append(detail)
                all_videos[v_id] = detail
                total_videos += 1

        # Summarize every video across every channel in as few Gemini calls as
        # possible, instead of one call per video.
        if all_videos:
            print(f"Summarizing {len(all_videos)} video(s) via batched Gemini calls...")
            self.summarize_descriptions_batch(all_videos)

        # Build HTML
        end_time = datetime.datetime.now(tz_eastern)
        date_str = start_time.strftime("%B %-d, %Y")
        start_str = start_time.strftime("%-I:%M:%S %p ET")
        end_str = end_time.strftime("%-I:%M:%S %p ET")
        video_word = "video" if total_videos == 1 else "videos"
        count_suffix = (
            f"({total_videos} new {video_word})" if total_videos else "(no new videos)"
        )
        subject = f"YouTube Digest - {date_str} {count_suffix}"

        html = "<html><body style='font-family: Arial, sans-serif;'>"
        html += "<h1>YouTube Daily Digest</h1>"
        html += f"<p>Date: {date_str} | Started: {start_str} | Completed: {end_str}</p>"

        if skipped_channels:
            html += (
                "<div style='margin: 15px 0; padding: 10px 15px; background-color: "
                "#fff3cd; border: 1px solid #ffeeba; border-radius: 4px; font-size: "
                "13px; color: #856404;'>"
            )
            html += (
                f"<strong>Note:</strong> {len(skipped_channels)} channel(s) could "
                "not be checked this run:"
            )
            html += "<ul style='margin: 5px 0 0 20px; padding: 0;'>"
            for reason in skipped_channels:
                html += f"<li>{escape(reason)}</li>"
            html += "</ul></div>"

        if total_videos == 0:
            html += f"<p>No new videos found in the last {LOOKBACK_HOURS} hours.</p>"
        else:
            # Randomize channel order on every run so no single channel always
            # leads the digest. Empty channels are already absent from
            # digest_data, so only channels with new videos are shuffled.
            channel_items = list(digest_data.items())
            random.shuffle(channel_items)
            for channel_key, videos in channel_items:
                channel_title = channel_titles.get(channel_key, channel_key)
                # Show a channel's videos oldest-first (ascending chronological).
                # Videos missing a publish time sort to the top.
                videos = sorted(videos, key=_publish_sort_key)
                html += f"<h2 style='border-bottom: 1px solid #ccc; padding-bottom: 5px; margin-top: 30px;'>{escape(channel_title)}</h2>"
                for video in videos:
                    html += "<div style='margin-bottom: 25px; line-height: 1.5;'>"
                    html += f"<div style='margin-bottom: 5px;'><strong><a href='{escape(video.link, quote=True)}' style='text-decoration: none; color: #1a0dab;'>{escape(video.title)}</a></strong> <span style='color: #666; font-size: 0.9em;'>({escape(video.duration)})</span></div>"
                    html += f"<div style='color: #444; font-size: 14px; white-space: normal; word-wrap: break-word; overflow-wrap: break-word;'>{escape(video.summary or '')}</div>"
                    html += "</div>"

        html += "</body></html>"
        self.send_email(html, subject)


if __name__ == "__main__":
    YouTubeDigest().run()
