from __future__ import annotations
import os


def youtube_api_key() -> str:
    """Returns the YouTube API key from environment variables."""
    return os.environ.get("YOUTUBE_API_KEY", "")


def gemini_api_key() -> str:
    """Returns the Gemini API key from environment variables."""
    return os.environ.get("GEMINI_API_KEY", "")


def gmail_user() -> str:
    """Returns the Gmail user email from environment variables."""
    return os.environ.get("GMAIL_USER", "")


def gmail_app_password() -> str:
    """Returns the Gmail app password from environment variables."""
    return os.environ.get("GMAIL_APP_PASSWORD", "")


def recipient_email() -> str:
    """Returns the recipient email from environment variables."""
    return os.environ.get("RECIPIENT_EMAIL", "")


def youtube_credentials() -> str | None:
    """Returns the YouTube OAuth credentials JSON string from environment variables."""
    return os.environ.get("YOUTUBE_CREDENTIALS")
