# Agent Guidelines: YTNotifier

This document gives the rules for AI agents that work on the YTNotifier repository. Obey these rules. They keep the code consistent and easy to maintain.

---

## 1. Development environment and commands

The project uses Python 3.12 or later. It uses uv to manage packages.

### 1.1 Dependencies

The file `pyproject.toml` lists the dependencies. The file `uv.lock` pins the versions. GitHub Actions installs from the lock file with `uv sync --frozen`.

| Task | Command |
|---|---|
| Install all dependencies, with test tools | `uv sync` |
| Install the same set as GitHub Actions | `uv sync --frozen --no-dev` |
| Add a dependency | `uv add package-name` |
| Add a test-only dependency | `uv add --dev package-name` |
| Install with pip (local only) | `pip install -r requirements.txt` |

When you add a dependency, also add it to `requirements.txt`. That file is a copy for people who use pip.

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
| `channels.yml` | The list of YouTube channels to monitor. |
| `tests/` | The pytest tests. |
| `.github/workflows/` | The GitHub Actions workflow files. |
| `pyproject.toml`, `uv.lock` | The dependencies and their pinned versions. |
| `requirements.txt` | A copy of the dependencies for pip. |

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

The project has four workflows in `.github/workflows/`:

| File | Workflow name | Input |
|---|---|---|
| `daily_digest.yml` | YouTube Daily Digest | None |
| `update_subscriptions.yml` | Update Subscriptions | None |
| `repair_channel_ids.yml` | Repair Channel IDs | `apply` (checkbox) |
| `add_channel.yml` | Add Channel | `url` (text) |

All four workflows start only with `workflow_dispatch`. No workflow has a `schedule:` trigger.

If you change a workflow file, test it. Run it from the **Actions** tab after you push.

**CAUTION:** In a workflow `run:` step, do not put `${{ inputs.xxx }}` in the command text. Put the input in an `env:` variable. Then use the variable in the command. This stops a bad input from changing the command.

### 7.3 Pull requests

Make sure Ruff and the tests pass before you merge.

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

- `pyproject.toml` is the source of truth. `uv.lock` pins the versions. GitHub Actions installs from the lock file with `uv sync --frozen`.
- To add a library:
  1. Run `uv add package-name`. This updates `pyproject.toml` and `uv.lock`.
  2. Add the same library to `requirements.txt`.
  3. If the library needs special setup, describe the setup in `README.md` or in this file.
- Do not add a heavy library if a small one does the job. Use the Python standard library for simple tasks, for example `smtplib` and `json`.

---

## 11. Specific instructions

- Before you add a feature, check if `pyproject.toml` and `uv.lock` need a change.
- If you change `digest.py`, make sure the HTML is still valid for email clients (see section 9).
- The Gemini prompts are written for the `gemini-3.1-flash-lite` model. Keep them short and clear.
- Run `uvx ruff format .` and `uvx ruff check .` before you finish a change.
- If you make a new file, give it a `snake_case` name, a module docstring, and type hints.
- Do not remove the `if __name__ == "__main__":` block from a script.
- Keep two blank lines between functions in `digest.py`.
