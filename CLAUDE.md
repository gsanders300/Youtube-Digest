# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

For detailed coding standards (naming, formatting, docstrings, error handling, HTML-email rules), see
`AGENTS.md` — it is the authoritative style guide for this repo and applies to Claude Code too.

## Commands

```bash
# Install deps (with test tools) / installs must also update requirements.txt when adding a package
uv sync
uvx ruff check .            # lint
uvx ruff check --fix .      # lint, autofix
uvx ruff format .           # format
uv run pytest                                          # all tests
uv run pytest tests/test_digest_run.py                 # one file
uv run pytest tests/test_add_channel.py::TestParseChannelUrl  # one class/function

# Run scripts locally (secrets come from .env; GitHub Actions uses repo Secrets instead)
uv run --env-file .env python digest.py
uv run --env-file .env python add_channel.py https://www.youtube.com/@Name
uv run --env-file .env python repair_channel_ids.py            # dry run
uv run --env-file .env python repair_channel_ids.py --write    # apply
uv run --env-file .env python get_subscriptions.py             # needs a browser, local only
```

## Architecture

**`youtube_api.py` is the shared YouTube API code; every script uses it.** `execute(request)` is the
only way to run a request (retry/backoff, no retry on quotaExceeded). `YouTubeDigest` takes its
clients through the constructor (`YouTubeDigest(youtube=..., genai_client=...)`), and tests inject
mocks this way. Put new YouTube API logic in `youtube_api.py`, not in a script.

**`channel_list.py` is the only code that reads or writes `channels.yml`**, the single source of
truth for monitored channels. Entries have two valid shapes: a bare `"@handle"` string, or a dict
with `handle`, `title`, `id`, `uploads_playlist_id`, and `digest` (default `True`). `load()` turns
both shapes into `Channel` models. `save()` writes handle-only entries back as bare strings, uses the
canonical key order, and keeps unknown keys. `merge_subscriptions` (used by `get_subscriptions.py`)
never removes entries. It refreshes matched entries (matched by id, then by handle ignoring case),
keeps their `digest` flags, and appends new subscriptions.

**Channel resolution lives in one place: `youtube_api.resolve_channel(youtube, handle=, channel_id=,
title=)`.** It tries the `@handle`, then the channel `id`, then a search by `title`. The search runs
only when a title is passed; it costs 100 quota units, and its hit must have exactly that title
(ignoring case). Every candidate's uploads playlist is probed before it is accepted, because a
channel can resolve yet expose an uploads playlist that 404s. Callers choose the steps they can
afford:
- `add_channel.py` passes only a handle or an id.
- `repair_channel_ids.py` passes all three.
- The digest passes handle and id, both when an entry has no `uploads_playlist_id` and when a stored
  one returns playlistNotFound (`_get_recent_videos_with_fallback`).
- The digest never writes a recovered ID back. `repair_channel_ids.py` does that.

**Summarization is batched, not per-video.** `YouTubeDigest.run` collects every new video across all
channels first, then calls `summarize_descriptions_batch`, which groups videos into
`GEMINI_BATCH_SIZE` chunks and asks Gemini for one JSON object per chunk (video ID → summary). Any
video missing from a chunk's parsed response falls back individually to `summarize_description`. When
touching summarization, preserve this batch-then-fallback structure — it's what keeps Gemini calls
low while guaranteeing every video still gets a summary.

**All four GitHub Actions workflows (`.github/workflows/`) are `workflow_dispatch`-only — none run on
a schedule.** `daily_digest.yml` and `update_subscriptions.yml` auto-file a GitHub issue on failure
(deduped by label) with the likely cause and remediation steps baked into the issue body; if you
change failure-handling logic in `digest.py` or `get_subscriptions.py`, keep those issue templates
accurate. Workflow inputs are always passed through `env:`, never interpolated directly into a `run:`
step (see `AGENTS.md` §7.2).

**Video ordering in the email is deliberately partly randomized, partly deterministic**: channel
sections are shuffled per run (`random.shuffle`) so no channel always leads, but videos within a
channel are always sorted oldest-first by `_publish_sort_key`.
