# YTNotifier

YTNotifier is a Python tool. It runs in GitHub Actions. Each day, it sends one email to you. The email lists the new videos from the YouTube channels that you select. Gemini AI writes a short summary of each video.

This document tells you how to install the tool, how to configure it, and how to run each command.

---

## 1. Get the API keys and secrets

The tool reads its keys and passwords from GitHub Secrets. To store a secret, go to your repository on GitHub. Then go to **Settings > Secrets and variables > Actions**. Select **New repository secret**.

### 1.1 Google Cloud (YouTube Data API)

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Make a new project. For example, give it the name `YTNotifier`.
3. Go to **APIs & Services > Library**.
4. Find "YouTube Data API v3". Select **Enable**.
5. Go to **APIs & Services > Credentials**.
6. Select **Create Credentials > API Key**.
7. Store the key in GitHub Secrets with the name `YOUTUBE_API_KEY`.

The next steps are optional. Do them only if you want to import your YouTube subscriptions with the `get_subscriptions.py` script (see section 3.1).

8. Go to **OAuth consent screen**. Select **External**. Write the app information that the form asks for.
9. Go to **Credentials**. Select **Create Credentials > OAuth client ID**. Select **Desktop app**.
10. Download the JSON file. Change the file name to `client_secrets.json`.
11. Put the file in the root folder of the project.

**CAUTION:** Do not commit `client_secrets.json` to git. The file contains a secret.

### 1.2 Google AI Studio (Gemini API)

1. Go to [Google AI Studio](https://aistudio.google.com/).
2. Select **Get API key**. Make a new key.
3. Store the key in GitHub Secrets with the name `GEMINI_API_KEY`.

### 1.3 Gmail

The tool sends the email through your Gmail account. Gmail needs an app password for this.

1. Go to your [Google Account security settings](https://myaccount.google.com/security).
2. Turn on **2-Step Verification**.
3. Find **App passwords**. Make a new app password with the name `YTNotifier`.
4. Store your Gmail address in GitHub Secrets with the name `GMAIL_USER`.
5. Store the 16-character app password in GitHub Secrets with the name `GMAIL_APP_PASSWORD`.

### 1.4 Recipient email

1. Store the email address that receives the digest in GitHub Secrets with the name `RECIPIENT_EMAIL`.

### 1.5 List of secrets

| Secret | Content | Used by |
|---|---|---|
| `YOUTUBE_API_KEY` | YouTube Data API key | Daily digest, Repair Channel IDs, Add Channel |
| `GEMINI_API_KEY` | Gemini API key | Daily digest |
| `GMAIL_USER` | Your Gmail address | Daily digest |
| `GMAIL_APP_PASSWORD` | Gmail app password | Daily digest |
| `RECIPIENT_EMAIL` | Address that receives the digest | Daily digest |
| `YOUTUBE_CREDENTIALS` | OAuth credentials JSON (see section 3.1) | Update Subscriptions |

---

## 2. Install the project on your computer

You need these programs on your computer:

- Python 3.12 or later.
- [uv](https://docs.astral.sh/uv/), the Python package manager.
- Git.

Do these steps:

1. Clone the repository.
   ```bash
   git clone https://github.com/gsanders300/Youtube-Digest.git
   cd Youtube-Digest
   ```
2. Install the dependencies.
   ```bash
   uv sync
   ```

**NOTE:** `uv sync` also installs the test tools. To install only what GitHub Actions installs, run `uv sync --frozen --no-dev`.

**NOTE:** If you do not use uv, run `pip install -r requirements.txt`. GitHub Actions does not use this method.

### 2.1 Store your keys in a `.env` file

The scripts read their keys from environment variables. You can store the keys in a file with the name `.env` in the root folder. Git ignores this file. uv loads the file when you give the `--env-file .env` option.

1. Open the file `.env` in the root folder. If the file does not exist, make it.
2. Write one variable on each line. Use this format:
   ```
   YOUTUBE_API_KEY=your-key
   ```
3. To run the digest on your computer, also add `GEMINI_API_KEY`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`, and `RECIPIENT_EMAIL`.
4. Save the file.
5. Put `--env-file .env` after `uv run` in each command. Example:
   ```bash
   uv run --env-file .env python add_channel.py https://www.youtube.com/@Name
   ```

**CAUTION:** Do not commit `.env` to git. Do not copy the key into a chat, an email, or a screenshot.

**NOTE:** To make uv load the file without the option, set the variable `UV_ENV_FILE` to the full path of the file. In PowerShell, run this command one time, then open a new terminal:
```powershell
[Environment]::SetEnvironmentVariable("UV_ENV_FILE", "C:\path\to\Youtube-Digest\.env", "User")
```
After this, every `uv run` command on your computer loads this file. If you use uv for other projects, this may not be what you want. If you set the variable, you can remove `--env-file .env` from the commands in this document.

---

## 3. Make the channel list

The file `channels.yml` is the channel list. The digest reads this file. Each entry has these fields:

```yaml
channels:
- handle: '@ericwtech'
  title: Eric Tech
  id: UCOXRjenlq9PmlTqd_JhAbMQ
  uploads_playlist_id: UUOXRjenlq9PmlTqd_JhAbMQ
  digest: true
```

| Field | Content |
|---|---|
| `handle` | The channel handle. It starts with `@`. |
| `title` | The channel name. The email shows this name. |
| `id` | The channel ID. It starts with `UC` and has 24 characters. |
| `uploads_playlist_id` | The uploads playlist ID. It starts with `UU` and has 24 characters. |
| `digest` | `true` to include the channel in the email. `false` to exclude it. If the field is not there, the value is `true`. |

An entry can also be only a handle, on one line:

```yaml
channels:
- '@ericwtech'
```

The digest finds the channel ID and the uploads playlist of this entry each time that it runs. This uses more API quota. A full entry is better.

The scripts that write `channels.yml` keep the two formats. They keep the order of the entries, the `digest` settings, and the fields that they do not know.

There are three ways to add channels to the list.

### 3.1 Import your YouTube subscriptions

This method needs a browser login. You must run it on your computer, not in GitHub Actions.

1. Make sure `client_secrets.json` is in the root folder (see section 1.1, steps 8 to 11).
2. Run the script.
   ```bash
   uv run python get_subscriptions.py
   ```
3. A browser window opens. Log in to your YouTube account. Give the permission that the page asks for.
4. The script adds all your subscriptions to `channels.yml`.
5. The script also shows a block of JSON text. This is your OAuth credentials. If you want the **Update Subscriptions** workflow to run in GitHub Actions, store this JSON in GitHub Secrets with the name `YOUTUBE_CREDENTIALS`.
6. Commit the channel list and push it.
   ```bash
   git add channels.yml
   git commit -m "Initial channel list from subscriptions"
   git push
   ```

**NOTE:** If you run this script again, it updates the subscriptions that are already in the file and adds new subscriptions at the end. It does not remove entries. It keeps the entries that you added by hand or with `add_channel.py`, and it keeps the `digest: false` settings. To stop the digest for a channel that you unsubscribed from, set `digest: false` or remove the entry by hand.

### 3.2 Add one channel from its URL

This method needs only the YouTube API key.

1. Make sure `YOUTUBE_API_KEY` is in your `.env` file (see section 2.1).
2. Run the script with the channel URL.
   ```bash
   uv run --env-file .env python add_channel.py https://www.youtube.com/@Name
   ```
3. Commit the channel list and push it.
   ```bash
   git add channels.yml
   git commit -m "Add channel"
   git push
   ```

The script accepts these forms of input:

- `https://www.youtube.com/@Name` (also with `/videos` or other text after the handle)
- `https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx`
- `youtube.com/@Name` (without `https://`)
- `@Name`
- `UCxxxxxxxxxxxxxxxxxxxxxx`

The script gets the handle, the title, the channel ID, and the uploads playlist ID from the YouTube API. It makes sure that the uploads playlist works. If the playlist does not work, the script stops and makes no change. It adds the entry to the end of the list with `digest: true`. If the channel is already in the list, the script makes no change.

You can also run this from GitHub. Use the **Add Channel** workflow (see section 5).

### 3.3 Edit the list by hand

1. Open `channels.yml` in a text editor.
2. Add, change, or remove entries. Keep the format shown above.
3. To keep a channel in the list but exclude it from the email, set `digest: false` on its entry.
4. Commit the file and push it.

---

## 4. Run the tools on your computer

### 4.1 Send the digest

The digest needs these environment variables: `YOUTUBE_API_KEY`, `GEMINI_API_KEY`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`, and `RECIPIENT_EMAIL`.

1. Make sure the five variables are in your `.env` file (see section 2.1).
2. Run the digest.
   ```bash
   uv run --env-file .env python digest.py
   ```

The tool sends one email. The email lists the videos published in the last 24 hours.

### 4.2 Check and repair the channel IDs

A channel entry can have a wrong ID. This happens, for example, when a person adds the entry by hand. The digest then cannot find the videos of that channel. This tool finds and corrects wrong IDs.

1. Make sure `YOUTUBE_API_KEY` is in your `.env` file (see section 2.1).
2. Run a check. This command makes no change to the file.
   ```bash
   uv run --env-file .env python repair_channel_ids.py
   ```
3. Read the report. Each entry shows `ok`, `FIX`, `UNRESOLVED`, or `ERROR`.
4. To write the corrections to `channels.yml`, run the command again with `--write`.
   ```bash
   uv run --env-file .env python repair_channel_ids.py --write
   ```
5. Commit the file and push it.

The tool finds each channel in this order:

1. By the `@handle`.
2. By the stored channel ID.
3. By a search for the `title`. The tool accepts the result only if its title is the same as the stored title (upper and lower case can be different). A search uses 100 units of API quota.

The tool accepts a channel only if its uploads playlist works. The tool does not check entries that have no channel ID, for example entries that are only a handle.

The tool keeps the order of the channels. It keeps the `digest` settings.

### 4.3 Add a channel

See section 3.2.

### 4.4 Import your subscriptions

See section 3.1.

---

## 5. Run the workflows in GitHub Actions

The project has four workflows. Each workflow starts only when you start it by hand. No workflow has a schedule.

| Workflow name | What it does | Input | Secrets that it needs |
|---|---|---|---|
| YouTube Daily Digest | Sends the digest email. | None | All five digest secrets |
| Update Subscriptions | Adds your current subscriptions to `channels.yml` and updates their entries. Commits the file. | None | `YOUTUBE_CREDENTIALS` |
| Repair Channel IDs | Checks the channel IDs. If you select **apply**, it corrects the file and commits it. | `apply` (checkbox) | `YOUTUBE_API_KEY` |
| Add Channel | Adds one channel from a URL. Commits the file. | `url` (text) | `YOUTUBE_API_KEY` |

To run a workflow:

1. Go to your repository on GitHub. Select the **Actions** tab.
2. Select the workflow name in the left column.
3. Select **Run workflow**.
4. If the workflow has an input, write or select the value.
5. Select the green **Run workflow** button.

**NOTE:** If the daily digest fails, the workflow opens a GitHub issue in your repository. The issue has the label `digest-error`. If Update Subscriptions fails, the workflow opens an issue with the label `credentials-error`. The issue tells you how to make new credentials. A workflow does not open a second issue while the first one is open.

### 5.1 Make the digest run each day

To make the digest run automatically, add a schedule to `.github/workflows/daily_digest.yml`. Put these lines under `on:`:

```yaml
  schedule:
    - cron: "0 12 * * *"
```

This example runs the digest at 12:00 UTC each day. Change the cron value to the time that you want. Then commit the file and push it.

### 5.2 Keep your channel list private

If you fork this public repository, your `channels.yml` is public too. To keep the list private, use two repositories:

- This public repository has the code.
- A private repository has your `channels.yml`, the workflow files, and the secrets.

Each workflow in the private repository gets the code from the public repository when it runs. The scripts read and write `channels.yml` in the folder where they run. Thus they use the list in the private repository.

1. Make a private repository. Put your `channels.yml` in its root folder.
2. Copy the files from `.github/workflows/` into the private repository.
3. In each workflow, add a second checkout step after the first one. Then change the `uv` commands as this example shows:
   ```yaml
   - name: Checkout repository
     uses: actions/checkout@v4

   - name: Checkout code
     uses: actions/checkout@v4
     with:
       repository: gsanders300/Youtube-Digest
       path: app

   - name: Install uv
     uses: astral-sh/setup-uv@v5
     with:
       enable-cache: true

   - name: Install dependencies
     run: uv sync --frozen --no-dev --project app

   - name: Run digest script
     run: uv run --frozen --no-dev --project app python app/digest.py
   ```
4. Store the secrets in the private repository (see section 1).

To run a script on your computer, clone both repositories into the same parent folder. Run the script from the private folder:

```bash
uv run --project ../Youtube-Digest --env-file .env python ../Youtube-Digest/digest.py
```

---

## 6. Test and check the code

Run these commands from the root folder of the project.

| Task | Command |
|---|---|
| Run all tests | `uv run pytest` |
| Run one test file | `uv run pytest tests/test_add_channel.py` |
| Run one test | `uv run pytest tests/test_add_channel.py::TestParseChannelUrl` |
| Check the code style | `uvx ruff check .` |
| Correct the code style | `uvx ruff check --fix .` |
| Format the code | `uvx ruff format .` |
| Add a dependency | `uv add package-name` |

**NOTE:** The `tzdata` package is a dependency. Windows needs it for time zone data. Do not remove it.

---

## 7. How the digest works

This section is for reference. You do not need to read it to use the tool.

- **Summaries.** The tool uses the `gemini-3.1-flash-lite` model. It sends several videos to Gemini in one request. If Gemini does not return a summary for a video, the tool asks again for that one video. Each video gets a summary.
- **Channels that the tool cannot check.** If a channel has a bad handle, no uploads playlist, or an API error, the tool continues with the other channels. The email shows a note with the names of the channels that it could not check.
- **Duplicate entries.** If two entries have the same uploads playlist, the tool uses only the first entry.
- **Wrong playlist IDs.** If a stored uploads playlist ID is wrong, the API returns `playlistNotFound`. The tool then finds the channel again from its `@handle`, then from its stored channel ID. It makes sure that the new playlist works, and then it continues. The tool does not write the correction to `channels.yml`. To correct the file, run the repair tool (see section 4.2).
- **API errors.** If the YouTube API returns a rate limit error or a server error, the tool waits and tries again. It tries again up to three times. All the scripts do this. If the API returns `quotaExceeded`, the tool does not try again. The quota resets each day.
