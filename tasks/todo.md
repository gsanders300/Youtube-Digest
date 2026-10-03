# Deepen channel resolution, YouTube client, and channel list (architecture review 1-3)

## Design
- `youtube_api.py` (new) — the YouTube client module + channel resolution module.
  - `execute(request)` — retry/backoff (moved from `YouTubeDigest._execute_youtube_request`).
  - `is_playlist_not_found(error)` — moved from `YouTubeDigest._is_playlist_not_found`.
  - `normalize_handle(custom_url)` — the one customUrl → `@handle` rule.
  - `resolve_channel(youtube, *, handle=None, channel_id=None, title=None) -> ResolvedChannel | None`
    - chain: `@handle` → `channel_id` → title search (only when `title` is given; search result must
      match the stored title, case-insensitively, before it is accepted).
    - one `channels.list(part="snippet,contentDetails")` per candidate; every candidate's uploads
      playlist is probed before acceptance.
    - `ResolvedChannel(id, handle, title, uploads_playlist_id, strategy)`.
- `channel_list.py` (new) — the only reader/writer of `channels.yml`.
  - `Channel` model (handle, title, id, uploads_playlist_id, digest=True; unknown keys preserved).
  - `load(path)` / `save(channels, path)`; bare `"@handle"` entries round-trip as bare strings;
    `save(load())` on today's file is byte-identical.
  - `add(channels, channel) -> bool` (dedup by id / handle case-insensitive; from `append_channel`).
  - `merge_subscriptions(existing, subscribed) -> list[Channel]` (digest flags carried by id, then
    handle case-insensitively; retention rule per decision below).
- `digest.py`: `YouTubeDigest(youtube=None, genai_client=None)` constructor injection; delete
  `_execute_youtube_request`, `_is_playlist_not_found`, `get_channel_id`, `get_uploads_playlist_id`;
  `run` reads via `channel_list.load`, resolves via `resolve_channel`; stale-playlist recovery goes
  through `resolve_channel` (so it is probed) and skips a recovered playlist already processed.
- `add_channel.py`, `repair_channel_ids.py`, `get_subscriptions.py`: no `YouTubeDigest.__new__`; use
  `youtube_api` + `channel_list`. `get_subscriptions` gains retry via `execute`.
- Out of scope: digest writing recovered IDs back to channels.yml (would need a committing workflow);
  email rendering (candidate 4); summarizer (candidate 5).

## Plan
- [x] `youtube_api.py` + `tests/test_youtube_api.py` (retry tests move here; resolution chain,
      title verification, probe rejection, customUrl normalization)
- [x] `channel_list.py` + `tests/test_channel_list.py` (replaces `tests/test_digest_filter.py`, which
      tested copies of the logic; round-trip test against the real `channels.yml`)
- [x] `digest.py` constructor injection + switch to both modules; update digest test fixtures
- [x] `add_channel.py`, `repair_channel_ids.py`, `get_subscriptions.py` switched; update tests
- [x] Update CLAUDE.md, AGENTS.md project structure, README if behavior changed; add CONTEXT.md terms
- [x] `uv run pytest`, `uvx ruff check .`, `uvx ruff format --check .`; `save(load())` diff check

## Review
- New modules: `youtube_api.py` (execute, is_playlist_not_found, normalize_handle, resolve_channel)
  and `channel_list.py` (Channel, load, save, add, merge_subscriptions). No script uses
  `YouTubeDigest.__new__` any more; `YouTubeDigest(youtube=, genai_client=)` takes injected clients.
- Removed: `YouTubeDigest._execute_youtube_request`, `_is_playlist_not_found`, `get_channel_id`,
  `get_uploads_playlist_id`, `ChannelConfig`; `repair_channel_ids._resolve/_uploads_*`;
  `add_channel.resolve_channel/append_channel`; `get_subscriptions.ChannelData`;
  `tests/test_digest_filter.py` (tested copies of production logic).
- Behavior changes (intended):
  - get_subscriptions merges instead of replacing: keeps every existing entry and its order,
    carries digest flags by id then case-insensitive handle, appends new subscriptions, and now
    retries API calls.
  - Title search only accepts a hit whose title matches the stored title (case-insensitive).
  - Digest recovery tries handle then stored id, probes before retrying, and skips a recovered
    playlist that another entry already covered.
  - A bare non-@ string entry is no longer searched by name at digest time; it is reported as
    "channel not found". None exist in channels.yml.
  - repair skips entries with no stored id (bare strings, as before; also handle-only dicts).
- Verification: 116 tests pass (HEAD baseline: 99). Ruff check and format are clean.
  `save(load())` on the real channels.yml is lossless (test_repo_channels_file_round_trips). Text
  differs only in that the misspelled `uploads_playlist_ID` key on `@Motormouth..` moves below
  `digest`.
- Not verified: a live API run. HTTPS failed with CERTIFICATE_VERIFY_FAILED in this environment
  (network sandbox). Verify with `uv run --env-file .env python repair_channel_ids.py` (dry run)
  and `add_channel.py @ericwtech` (expect "already in channels.yml").

---

# Add channel by URL

## Plan
- [x] Create `add_channel.py` (parse URL, resolve via API, append to channels.yml)
- [x] Create `.github/workflows/add_channel.yml` (workflow_dispatch with `url` input, commits channels.yml)
- [x] Create `tests/test_add_channel.py`
- [x] Update `README.md` Maintenance section
- [x] Update `AGENTS.md` project structure
- [x] Run pytest and ruff

## Review
- `add_channel.py` accepts `youtube.com/@Name`, `youtube.com/channel/UC...`, bare `@Name`, bare `UC...`.
  One `channels().list(part="snippet,contentDetails")` call resolves handle, title, id and uploads
  playlist; the uploads playlist is probed via `repair_channel_ids._uploads_playlist_works` before
  writing. Duplicates (by id or handle) exit 0 without rewriting the file.
- Workflow passes the URL through an env var (never inline `${{ inputs.url }}` in `run:`) and reuses
  the commit block from `repair_channel_ids.yml`.
- 42 new tests pass; ruff check/format clean on the new files.
- Pre-existing, unrelated: 9 tests in `tests/test_digest_run.py` fail locally on Windows/Python 3.14
  with `ZoneInfoNotFoundError: America/New_York` because `tzdata` is not installed. They fail
  identically on a clean checkout.
- Not verified locally: a live API run (no `YOUTUBE_API_KEY` in the local environment). Verify by
  running `add_channel.py @ericwtech` (expect "already in channels.yml") once a key is available, or
  by dispatching the Add Channel workflow.
- Noted for the owner: `channels.yml:440` has a misspelled `uploads_playlist_ID` key on the
  `@Motormouth..` entry, so that channel re-resolves its playlist on every digest run.
