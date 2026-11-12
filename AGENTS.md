# Agent Guidelines: Youtube-Digest

This document gives the rules for AI agents that work on the Youtube-Digest repository. Obey these rules. They keep the code consistent and easy to maintain.

---

## 1. Development environment and commands

The project uses Python 3.12 or later. It uses uv to manage packages.

### 1.1 Dependencies

The file `pyproject.toml` lists the dependencies. The file `uv.lock` pins the versions. The digest workflows install from the lock file with `uv sync --frozen`. The **Tests** workflow uses `--locked`, which also fails when `uv.lock` does not agree with `pyproject.toml`. Dependabot updates both files every week (section 7.2).

| Task | Command |
|---|---|
| Install all dependencies, with test tools | `uv sync` |
| Install the same set as GitHub Actions | `uv sync --frozen --no-dev` |
| Add a dependency | `uv add package-name` |
| Add a test-only dependency | `uv add --dev package-name` |

### 1.2 Run the scripts

Run all scripts from the root folder of the project. The scripts open `channels.yml` with a relative path.

On a developer's computer, the keys are in the file `.env` in the root folder. Git ignores this file. To load the keys, put `--env-file .env` after `uv run`. Example: `uv run --env-file .env python add_channel.py https://www.youtube.com/@Name`. GitHub Actions does not use this file. It reads the keys from GitHub Secrets.

| Task | Command |
|---|---|
| Send the daily digest | `uv run python digest.py` |
| Import YouTube subscriptions (local only, needs a browser) | `uv run python get_subscriptions.py` |
| Check the channel IDs (no change to the file) | `uv run python repair_channel_ids.py` |
| Correct the channel IDs in the file | `uv run python repair_channel_ids.py --write` |
| Add one channel from its URL | `uv run python add_channel.py https://www.youtube.com/@Name` |

### 1.3 Lint and format

The project uses Ruff. Ruff is not in the dependencies. Run it with `uvx`.

| Task | Command |
|---|---|
| Check the code | `uvx ruff check .` |
| Correct the problems that Ruff can correct | `uvx ruff check --fix .` |
| Format the code | `uvx ruff format .` |
| Check the format without a change | `uvx ruff format --check .` |

### 1.4 Tests

The project uses pytest. The tests are in the `tests/` folder.

| Task | Command |
|---|---|
| Run all tests | `uv run pytest` |
| Run one test file | `uv run pytest tests/test_digest_run.py` |
| Run one test class or function | `uv run pytest tests/test_add_channel.py::TestParseChannelUrl` |
| Measure coverage (first run `uv add --dev pytest-cov`) | `uv run pytest --cov=.` |

---

## 2. Project structure

| File or folder | Content |
|---|---|
| `digest.py` | The main script. It makes and sends the daily email. |
| `get_subscriptions.py` | Gets the user's subscriptions with OAuth2. Merges them into `channels.yml`. |
| `repair_channel_ids.py` | Finds and corrects wrong channel IDs in `channels.yml`. Makes no change unless you give `--write`. |
| `add_channel.py` | Adds one channel to `channels.yml` from its YouTube URL. Gets the handle, title, channel ID, and uploads playlist ID from the API. |
| `youtube_api.py` | Shared YouTube API code: requests with retry, and channel resolution (handle, then ID, then title search). |
| `channel_list.py` | The only code that reads and writes `channels.yml`. Merges subscriptions into the list. |
| `environment.py` | Reads the environment variables. |
| `channels.yml` | A sample list of YouTube channels to monitor. `tests/test_channel_list.py` checks that `save(load())` writes it back unchanged. |
| `tests/` | The pytest tests. |
| `.github/workflows/` | The GitHub Actions workflow files. |
| `pyproject.toml`, `uv.lock` | The dependencies and their pinned versions. |

### 2.1 Shared YouTube API code

`youtube_api.py` has the YouTube API code that all scripts share. Put new YouTube API logic in this file, not in a script.

- `execute(request)` is the only way to run a request. It tries again with backoff after a rate limit or a server error. It does not try again after `quotaExceeded`.
- `YouTubeDigest` gets its clients from its constructor: `YouTubeDigest(youtube=..., genai_client=...)`. The tests give mock clients this way.

### 2.2 Channel list

`channel_list.py` is the only code that reads or writes `channels.yml`. That file is the single source of truth for the monitored channels.

- An entry has one of two shapes: a bare `"@handle"` string, or a dict with `handle`, `title`, `id`, `uploads_playlist_id`, and `digest` (default `True`).
- `load()` turns both shapes into `Channel` models.
- `save()` writes handle-only entries back as bare strings. It uses the canonical key order and keeps unknown keys.
- `merge_subscriptions()` (used by `get_subscriptions.py`) never removes entries. It refreshes the matched entries (matched by `id`, then by handle with case ignored), keeps their `digest` flags, and appends new subscriptions.

### 2.3 Channel resolution

All channel resolution is in `youtube_api.resolve_channel(youtube, handle=, channel_id=, title=)`. It tries the `@handle`, then the channel `id`, then a search by `title`.

- The search runs only when the caller gives a title. It costs 100 quota units. The function accepts the result only if its title is the same as the given title (case ignored).
- The function probes the uploads playlist of each candidate before it accepts the candidate. A channel can resolve but have an uploads playlist that returns 404.

Each caller uses only the steps that it can afford:

- `add_channel.py` gives only a handle or an ID.
- `repair_channel_ids.py` gives all three.
- The digest gives the handle and the ID. It does this when an entry has no `uploads_playlist_id`, and when a stored one returns `playlistNotFound` (`_get_recent_videos_with_fallback`).
- The digest never writes a recovered ID back to `channels.yml`. `repair_channel_ids.py` does that.

### 2.4 Summaries

The digest makes summaries in batches, not one video at a time.

1. `YouTubeDigest.run` collects all new videos from all channels first.
2. It calls `summarize_descriptions_batch`. That function puts the videos into groups of `GEMINI_BATCH_SIZE`. For each group, it asks Gemini for one JSON object that maps each video ID to its summary. After a Gemini server error (5xx), it waits `GEMINI_BATCH_RETRY_DELAY_SECONDS` and tries the group once more.
3. If a video is missing from the parsed response, the digest calls `summarize_description` for that one video.

Keep this batch-then-fallback structure. It keeps the number of Gemini calls low, and each video still gets a summary.

### 2.5 Order of the videos in the email

- The digest shuffles the channel sections on each run (`random.shuffle`), so no channel is always first.
- In each channel section, the videos are always sorted oldest first by `_publish_sort_key`.

---

## 3. Coding standards

### 3.1 Names

- Functions and variables: use `snake_case`.
- Constants: use `UPPER_CASE`.
- Classes: use `PascalCase`.
- Files: use `snake_case.py`.

### 3.2 Format

- Obey PEP 8.
- A line must not have more than 100 characters.
- Use double quotes for strings. Use single quotes only if the string contains double quotes.
- Put two blank lines between top-level definitions.

### 3.3 Imports

Put the imports in three groups, in this order:

1. Standard library.
2. Third-party libraries.
3. Local modules.

Sort the imports in each group by name. Ruff does this for you. Use absolute imports, not relative imports.

### 3.4 Type hints

Every function signature must have type hints for all parameters and for the return value. Example:

```python
def get_channel_id(handle: str) -> str | None:
    ...
```

### 3.5 Docstrings

Every module, class, and function must have a docstring. Use triple double quotes. Use the Google style. Describe the arguments, the return value, and the exceptions that the function raises.

---

## 4. Errors and logging

- Do not write a bare `except:` block. Catch specific exceptions.
- Put network calls (YouTube API, Gmail SMTP, Gemini API) in `try...except` blocks.
- Scripts that run in GitHub Actions must print clear error messages.
- If a required secret or setting is missing, stop early. Print a message that names the missing item.
- Do not call `sys.exit()` in a utility function. Return an error value or raise an exception. Call `sys.exit()` only in `main()`.

---

## 5. Configuration and secrets

- Do not write a secret in the code. Read secrets with the functions in `environment.py`.
- The project needs these secrets:
  - `YOUTUBE_API_KEY`
  - `GEMINI_API_KEY`
  - `GMAIL_USER`
  - `GMAIL_APP_PASSWORD`
  - `RECIPIENT_EMAIL`
  - `YOUTUBE_CREDENTIALS` (only for `get_subscriptions.py` in GitHub Actions)
- `DIGEST_SINCE` is not a secret. It is the ISO 8601 start time of the last successful digest run. `daily_digest.yml` sets it. When it is empty, the digest uses the last `LOOKBACK_HOURS` hours.
- Settings that are not secret, for example the channel list, go in `channels.yml`.

---

## 6. Test strategy

- Write tests for the logic in `digest.py` and in the utility scripts.
- Mock all external calls (YouTube, Gemini, SMTP). Use `unittest.mock` or `pytest-mock`.
- A test must give the same result each time. A test must not need a real API key or a network connection.
- Put all tests in the `tests/` folder.

---

## 7. Git and CI/CD

### 7.1 Commit messages

Write commit messages in the imperative mood. Example: "Add type hints to digest.py".

### 7.2 GitHub Actions

The project has these workflows in `.github/workflows/`:

| File | Workflow name | Input |
|---|---|---|
| `daily_digest.yml` | YouTube Daily Digest | None |
| `update_subscriptions.yml` | Update Subscriptions | None |
| `repair_channel_ids.yml` | Repair Channel IDs | `apply` (checkbox) |
| `add_channel.yml` | Add Channel | `url` (text) |
| `tests.yml` | Tests | None |
| `private-digest.yml` | Private digest | `task`, `url`, `apply`, `ref` (`workflow_call`) |
| `release.yml` | Release | None (runs on each push) |
| `dependabot-auto-merge.yml` | Dependabot auto-merge | None (runs on Dependabot pull requests) |
| `dependabot-watch.yml` | Dependabot watch | None (daily `schedule:`) |

`private-digest.yml` is the reusable workflow that the owner's private repository calls for all four digest workflows (README section 5.2). It runs the latest release, never `main`, and runs the tests first. The private repository calls it from `main`, so a change to it goes live at the next run with no release and no tests: keep its inputs and secret names stable, and keep its issue texts in step with the four digest workflows here.

The four digest workflows start only with `workflow_dispatch`. Do not give them a `schedule:` trigger. The only scheduled workflow is `dependabot-watch.yml`. `tests.yml` runs `pytest` with `--locked` on each push to `main` and on each pull request, so a stale `uv.lock` fails it. It has read-only permissions and uses no secrets. The README shows its status badge. `release.yml` makes a release on each push to `main` without waiting for Tests, so a stale lock pushed directly to `main` is still released. The private workflow runs the tests before it runs a script.

Every `uses:` is pinned to a commit SHA with a version comment. Keep new ones pinned.

Dependency updates are automatic. `.github/dependabot.yml` opens grouped pull requests for the Python packages and for the GitHub Actions every Saturday at 3am ET, and ignores major versions. The `uv` entry has `allow: dependency-type: all`, so Dependabot checks every package in `uv.lock`, not only the direct dependencies. `dependabot-auto-merge.yml` turns on auto-merge with the `DEPENDABOT_AUTOMERGE_TOKEN` Dependabot secret, a fine-grained token. A merge made with `GITHUB_TOKEN` would not start `release.yml`. A ruleset on `main` requires the `test` check, so the merge waits for Tests. The merge is a push, so `release.yml` makes the next patch release, and the private digest runs it at its next run. The auto-merge job sets the commit message itself, because `release.yml` reads `#minor` and `#major` from it. That job holds a write token, so it must never check out or run the pull request's code. `dependabot-watch.yml` opens an issue labeled `dependabot-stuck` when a Dependabot pull request stays open for more than one day. Dependabot security updates are not limited by the major-version rule.

All four digest workflows open a GitHub issue when they fail. Each issue has a label, and a workflow does not open a second issue while one is open. The issue text gives the likely cause and the steps to fix it. If you change the failure handling in `digest.py` or `get_subscriptions.py`, keep these issue texts correct.

If you change a workflow file, test it. Run it from the **Actions** tab after you push.

**CAUTION:** In a workflow `run:` step, do not put `${{ inputs.xxx }}` in the command text. Put the input in an `env:` variable. Then use the variable in the command. This stops a bad input from changing the command.

### 7.3 Pull requests

Make sure Ruff and the tests pass before you merge.

### 7.4 Releases

The project uses semantic versioning (`MAJOR.MINOR.PATCH`). `release.yml` makes a release on each push to `main`. It reads the latest `vX.Y.Z` tag and adds 1 to the patch number. If the commit message contains `#minor` or `#major`, it adds 1 to the minor or major number instead. The commit message becomes the release notes. The `version` in `pyproject.toml` is not used for releases.

- Do not run `gh release create` by hand.
- Do not write `#minor` or `#major` in a commit message unless you want that release type. This includes text that only describes the tokens.
- Each push to `main` is a release that the private digest runs at its next run. Put related changes in one push.

---

## 8. Agent workflow

Do these steps for each task:

1. **Analyze.** Find the related code and settings. Use `grep` or `glob`.
2. **Check the environment.** Read `pyproject.toml` and `uv.lock`.
3. **Implement.**
   - Obey the standards in section 3.
   - Give every new function type hints and a docstring.
   - Keep two blank lines between top-level definitions.
4. **Check quality.**
   - Run `uvx ruff check .` and `uvx ruff format .` on the files that you changed.
   - If you changed logic, run the tests. Add new tests in `tests/`.
5. **Verify.** Make sure each `if __name__ == "__main__":` block still works.

---

## 9. HTML for email

The `digest.py` script makes HTML for email clients. Email clients have limits. Obey these rules:

- Use only the `style` attribute for CSS. Do not use `<style>` blocks. Do not use external CSS.
- If a layout is complex, use tables. Email clients show tables correctly.
- Use web-safe fonts, for example Arial or sans-serif.
- Use absolute URLs for all links.
- Escape all text that you put in the HTML. Use `html.escape`.

---

## 10. Dependencies

- `pyproject.toml` is the source of truth. `uv.lock` pins the versions. The digest workflows install from the lock file with `uv sync --frozen`. The **Tests** workflow uses `--locked`.
- To add a library:
  1. Run `uv add package-name`. This updates `pyproject.toml` and `uv.lock`.
  2. If the library needs special setup, describe the setup in `README.md` or in this file.
- Do not add a heavy library if a small one does the job. Use the Python standard library for simple tasks, for example `smtplib` and `json`.

---

## 11. Specific instructions

- Before you add a feature, check if `pyproject.toml` and `uv.lock` need a change.
- If you change `digest.py`, make sure the HTML is still valid for email clients (see section 9).
- The Gemini prompts are written for the `gemini-3.5-flash-lite` model (`GEMINI_MODEL`). Keep them short and clear.
- Every Gemini call goes through `YouTubeDigest._generate_content`. It tries `GEMINI_MODEL` and, on a `google.genai.errors.APIError` or an `httpx2.TimeoutException`, tries `GEMINI_FALLBACK_MODEL` (`gemini-3.8-flash`) once with the same prompt and config. Each request times out after `GEMINI_TIMEOUT_SECONDS`. The SDK uses `httpx2`, so `httpx2` is a direct dependency.
- Run `uvx ruff format .` and `uvx ruff check .` before you finish a change.
- If you make a new file, give it a `snake_case` name, a module docstring, and type hints.
- Do not remove the `if __name__ == "__main__":` block from a script.
- Keep two blank lines between functions in `digest.py`.
