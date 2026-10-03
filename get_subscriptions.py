from __future__ import annotations
import json
import os
import sys

import google_auth_oauthlib.flow
import googleapiclient.discovery
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

import channel_list
import environment
from youtube_api import execute, normalize_handle

# The scopes required to view subscriptions
SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]


class SubscriptionFetcher:
    """Service for fetching and saving YouTube subscriptions."""

    def __init__(self) -> None:
        """Initializes the SubscriptionFetcher service."""
        self.api_service_name = "youtube"
        self.api_version = "v3"

    def get_credentials(self) -> Credentials:
        """Gets valid user credentials from environment or interactive flow."""
        creds = None

        # Check for credentials in environment variable (for GitHub Actions)
        env_creds = environment.youtube_credentials()
        if env_creds:
            print("Using credentials from environment...")
            try:
                creds_data = json.loads(env_creds)

                # Validate required fields
                required_fields = ["refresh_token", "client_id", "client_secret"]
                missing_fields = [
                    field for field in required_fields if field not in creds_data
                ]
                if missing_fields:
                    raise ValueError(
                        f"YOUTUBE_CREDENTIALS is missing required fields: {', '.join(missing_fields)}. "
                        f"Run 'python get_subscriptions.py' locally to generate valid credentials."
                    )

                creds = Credentials.from_authorized_user_info(creds_data, SCOPES)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"YOUTUBE_CREDENTIALS is not valid JSON: {e}. "
                    f"Ensure the secret contains a properly formatted JSON string."
                ) from e

        # If there are no valid credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                print("Refreshing expired credentials...")
                try:
                    creds.refresh(Request())
                    print("Credentials refreshed successfully!")
                except Exception as e:
                    raise ValueError(
                        f"Failed to refresh credentials: {e}. "
                        f"The refresh token may have expired. "
                        f"Run 'python get_subscriptions.py' locally to re-authenticate."
                    ) from e
            else:
                print("Starting interactive auth flow...")
                client_secrets_file = "client_secrets.json"
                if not os.path.exists(client_secrets_file):
                    raise FileNotFoundError(
                        f"{client_secrets_file} not found for interactive login."
                    )

                flow = (
                    google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
                        client_secrets_file, SCOPES
                    )
                )
                creds = flow.run_local_server(port=0)

                # Print only after a local login. A refresh keeps the same refresh token,
                # and printing it in CI would expose it in the Actions log.
                print("\n--- SAVE THIS TO GITHUB SECRETS AS 'YOUTUBE_CREDENTIALS' ---")
                print(creds.to_json())
                print("-----------------------------------------------------------\n")

        return creds

    def run(self) -> None:
        """Main execution logic to fetch and save subscriptions."""
        # Disable OAuthlib's HTTPs verification when running locally.
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

        try:
            credentials = self.get_credentials()
        except Exception as e:
            print(f"Error obtaining credentials: {e}")
            sys.exit(1)

        youtube = googleapiclient.discovery.build(
            self.api_service_name, self.api_version, credentials=credentials
        )

        subscribed: list[channel_list.Channel] = []
        next_page_token = None

        print("Fetching subscriptions...")
        while True:
            request = youtube.subscriptions().list(
                part="snippet", mine=True, maxResults=50, pageToken=next_page_token
            )
            response = execute(request)

            for item in response.get("items", []):
                title = item["snippet"]["title"]
                channel_id = item["snippet"]["resourceId"]["channelId"]

                # Fetch channel details to get the handle and uploads playlist
                chan_req = youtube.channels().list(
                    part="snippet,contentDetails", id=channel_id
                )
                chan_resp = execute(chan_req)
                if not chan_resp.get("items"):
                    continue

                item_details = chan_resp["items"][0]
                handle = normalize_handle(item_details["snippet"].get("customUrl"))
                uploads_playlist_id = item_details["contentDetails"][
                    "relatedPlaylists"
                ].get("uploads")

                subscribed.append(
                    channel_list.Channel(
                        handle=handle,
                        title=title,
                        id=channel_id,
                        uploads_playlist_id=uploads_playlist_id,
                    )
                )

                if handle:
                    print(f"Found: {handle} ({title})")
                else:
                    print(f"Found: {channel_id} ({title})")

            next_page_token = response.get("nextPageToken")
            if not next_page_token:
                break

        # Merge into the existing list rather than replacing it, so entries added
        # by hand or with add_channel.py, and every digest flag, survive.
        existing = (
            channel_list.load() if os.path.exists(channel_list.CHANNELS_FILE) else []
        )
        channel_list.save(channel_list.merge_subscriptions(existing, subscribed))

        print(f"\nSuccess! Found {len(subscribed)} subscriptions.")
        print("Your channels.yml has been updated.")


if __name__ == "__main__":
    SubscriptionFetcher().run()
