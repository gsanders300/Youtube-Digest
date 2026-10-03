"""Tests for credential validation in get_subscriptions.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from get_subscriptions import SubscriptionFetcher


class TestCredentialValidation:
    """Test credential validation and error handling."""

    def test_missing_refresh_token(self) -> None:
        """Test that missing refresh_token raises ValueError with helpful message."""
        fetcher = SubscriptionFetcher()

        # Mock environment variable with incomplete credentials
        incomplete_creds = json.dumps(
            {
                "client_id": "test-client-id",
                "client_secret": "test-client-secret",
                # Missing refresh_token
            }
        )

        with patch("environment.youtube_credentials", return_value=incomplete_creds):
            with pytest.raises(ValueError) as exc_info:
                fetcher.get_credentials()

            assert "refresh_token" in str(exc_info.value)
            assert "Run 'python get_subscriptions.py' locally" in str(exc_info.value)

    def test_missing_multiple_fields(self) -> None:
        """Test that missing multiple required fields are all reported."""
        fetcher = SubscriptionFetcher()

        # Mock environment variable with no required fields
        incomplete_creds = json.dumps({"some_other_field": "value"})

        with patch("environment.youtube_credentials", return_value=incomplete_creds):
            with pytest.raises(ValueError) as exc_info:
                fetcher.get_credentials()

            error_msg = str(exc_info.value)
            assert "refresh_token" in error_msg
            assert "client_id" in error_msg
            assert "client_secret" in error_msg

    def test_invalid_json(self) -> None:
        """Test that invalid JSON raises ValueError with helpful message."""
        fetcher = SubscriptionFetcher()

        # Mock environment variable with invalid JSON
        invalid_json = "not valid json {{"

        with patch("environment.youtube_credentials", return_value=invalid_json):
            with pytest.raises(ValueError) as exc_info:
                fetcher.get_credentials()

            assert "not valid JSON" in str(exc_info.value)

    def test_valid_credentials_format(self) -> None:
        """Test that valid credentials are accepted."""
        fetcher = SubscriptionFetcher()

        # Mock environment variable with valid credentials
        valid_creds = json.dumps(
            {
                "refresh_token": "1//test-refresh-token",
                "client_id": "test-client-id",
                "client_secret": "test-client-secret",
                "token": "test-access-token",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": ["https://www.googleapis.com/auth/youtube.readonly"],
            }
        )

        with patch("environment.youtube_credentials", return_value=valid_creds):
            with patch(
                "google.oauth2.credentials.Credentials.from_authorized_user_info"
            ) as mock_creds:
                # Mock the credentials object
                mock_cred_obj = MagicMock()
                mock_cred_obj.valid = True
                mock_creds.return_value = mock_cred_obj

                creds = fetcher.get_credentials()

                # Verify credentials were created
                assert creds is not None
                mock_creds.assert_called_once()

    def test_refresh_token_failure(self) -> None:
        """Test that refresh token failure raises helpful error."""
        fetcher = SubscriptionFetcher()

        # Mock environment variable with valid but expired credentials
        expired_creds = json.dumps(
            {
                "refresh_token": "1//expired-refresh-token",
                "client_id": "test-client-id",
                "client_secret": "test-client-secret",
                "token": "expired-access-token",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": ["https://www.googleapis.com/auth/youtube.readonly"],
            }
        )

        with patch("environment.youtube_credentials", return_value=expired_creds):
            with patch(
                "google.oauth2.credentials.Credentials.from_authorized_user_info"
            ) as mock_creds:
                # Mock expired credentials
                mock_cred_obj = MagicMock()
                mock_cred_obj.valid = False
                mock_cred_obj.expired = True
                mock_cred_obj.refresh_token = "1//expired-refresh-token"
                mock_cred_obj.refresh.side_effect = Exception("Invalid grant")
                mock_creds.return_value = mock_cred_obj

                with pytest.raises(ValueError) as exc_info:
                    fetcher.get_credentials()

                error_msg = str(exc_info.value)
                assert "Failed to refresh credentials" in error_msg
                assert "refresh token may have expired" in error_msg
                assert "Run 'python get_subscriptions.py' locally" in error_msg

    def test_refresh_does_not_print_credentials(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test that a successful refresh never prints the refresh token or client secret."""
        fetcher = SubscriptionFetcher()

        creds_json = json.dumps(
            {
                "refresh_token": "1//test-refresh-token",
                "client_id": "test-client-id",
                "client_secret": "test-client-secret",
                "token": "expired-access-token",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": ["https://www.googleapis.com/auth/youtube.readonly"],
            }
        )

        with (
            patch("environment.youtube_credentials", return_value=creds_json),
            patch(
                "google.oauth2.credentials.Credentials.from_authorized_user_info"
            ) as mock_creds,
        ):
            mock_cred_obj = MagicMock()
            mock_cred_obj.valid = False
            mock_cred_obj.expired = True
            mock_cred_obj.refresh_token = "1//test-refresh-token"
            mock_cred_obj.to_json.return_value = creds_json
            mock_creds.return_value = mock_cred_obj

            fetcher.get_credentials()

            mock_cred_obj.refresh.assert_called_once()
            output = capsys.readouterr().out
            assert "1//test-refresh-token" not in output
            assert "test-client-secret" not in output
