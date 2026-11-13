# Phase 6: Recording — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record a programme, a series or every game of a team from the guide to files on the library disk, play them back in StreamHub, and have them appear in the library server too.

**Architecture:** A `RecorderService` owns schedule and state in two tables; a scheduler tick starts and stops captures. A capture is a codec-copy ffmpeg process fed either from the channel's existing shared upstream (when someone is already watching it, so no extra source connection is used) or from its own admitted connection. Rules are re-evaluated against the guide after each guide refresh. Finished files are named so a library server recognises them, and StreamHub also lists and plays them itself.

**Tech Stack:** APScheduler, ffmpeg (codec copy to Matroska), SQLAlchemy/Alembic, the existing shared-upstream proxy and sports classifier.

## Global Constraints

- Recording is off until `RECORDINGS_DIR` is configured and writable; no directory is created outside it.
- Capture never transcodes. Padding: start 2 minutes early; end 5 minutes late, 60 minutes late for sports.
- Stop and mark `failed` when free space under `RECORDINGS_DIR` falls below `RECORDINGS_MIN_FREE_GB` (default 50).
- A recording shares an upstream when one exists for the channel; otherwise it must be admitted like any viewer. If no connection is free at start, retry every minute for 10 minutes, then mark `missed`; where the channel advertises a provider archive, fetch the missed programme from the archive afterwards.
- Scheduling needs `recordings.schedule`; listing and playback need `recordings.view`; a recording is visible to users whose rating ceiling allows the programme.
- Tests first with a fake clock and stubbed ffmpeg; one real-ffmpeg test on a generated stream, skipped when ffmpeg is absent.

---

### Task 1: Model and scheduling logic
**Files:** `hub-server/app/infra/models.py`, new Alembic revision, create `hub-server/app/services/recorder.py`, `services/recording_policy.py`; tests `tests/test_recorder_schedule.py`.

Tables:
- `recording_rule`: `id`, `profile_id`, `kind` (`series|team|keyword`), `match` (series title, team name or keyword), `channel_id` (nullable = any channel), `new_only` bool, `keep` (nullable = keep all), `enabled`, `created_at`.
- `recording`: `id`, `profile_id`, `rule_id` (nullable), `channel_id`, `programme_key` (source feed + channel + start, unique), `title`, `subtitle`, `season`, `episode`, `is_sport`, `starts_at`, `ends_at`, `pad_before_s`, `pad_after_s`, `state` (`scheduled|recording|done|failed|missed|cancelled`), `error`, `path`, `size_bytes`, `created_at`, `updated_at`.

```python
class RecorderService:
    async def schedule_programme(self, p: Principal, programme_id: str) -> Recording
    async def cancel(self, p: Principal, recording_id: str) -> None
    async def add_rule(self, p: Principal, kind: str, match: str, channel_id: str | None, new_only: bool, keep: int | None) -> RecordingRule
    async def apply_rules(self) -> int                 # after guide refresh: create recordings for matching future programmes
    async def tick(self, now: datetime) -> None        # start due, stop finished, retry waiting, enforce keep and free space
    async def list(self, p: Principal, *, state: str | None) -> list[Recording]
```
Matching: `series` = normalised programme title equality; `team` = the sports classifier says it is a game and the team name appears in title or subtitle; `keyword` = case-insensitive substring. `new_only` skips programmes the guide marks as repeats and programme keys already recorded. Overlapping duplicates of the same event on several channels: prefer the channel named in the rule, else the first by channel number.
- [ ] Tests: each rule kind, dedupe, padding, keep pruning, retry window → `missed`.

### Task 2: Capture
**Files:** create `hub-server/app/proxy/capture.py`; modify `proxy/stream_proxy.py` (a subscriber API on `SharedUpstream`), `services/recorder.py`; tests.

- `Capture.start(recording, source)` : if the channel has a live `SharedUpstream`, subscribe and pipe its bytes into `ffmpeg -i pipe:0 -c copy -f matroska <tmp>`; otherwise admit against the source and let ffmpeg read the stream URL directly (reconnect options on). On stop: finalise, move to the final name, record size.
- File naming under `RECORDINGS_DIR`: series `<Title>/Season NN/<Title> - SNNENN - <Subtitle>.mkv` (or `<Title> - YYYY-MM-DD.mkv` when numbers are unknown); sports `Sports/<Title> - <Subtitle> (YYYY-MM-DD).mkv`; everything else `<Title> (YYYY-MM-DD HHmm).mkv`. Sanitise for the filesystem.
- A capture on its own connection is tagged so `source_busy` reports "Recording: <title>".
- Archive recovery for `missed`: when the channel has a provider archive covering the programme, download it with the existing catch-up URL builder after the programme has ended.
- [ ] Tests: argument assertions, shared-upstream path does not call admission, own-connection path does, naming table, free-space stop.

### Task 3: API and guide integration
`api/routers/recordings.py`, schemas, contract, tests.
- `GET /recordings?state=`, `POST /recordings {programme_id}`, `DELETE /recordings/{id}` (cancel or delete file), `GET /recording-rules`, `POST /recording-rules`, `PATCH`, `DELETE`.
- Guide and programme payloads gain `recording_state`.
- Finished recordings are playable: `POST /playback/resolve {id: "rec:<id>"}` serves the file through the proxy with Range, and through the HLS path for browsers that cannot decode it.
- [ ] API and contract tests.

### Task 4: Web
- Guide and channel "now/next": **Record** on a programme → This programme / Every new episode / Every game for <team> (offered when the classifier finds teams).
- **Recordings** screen: Upcoming, Recorded (play, delete), Rules (edit, disable, delete); badges for failed or missed with the reason.
- Playwright on the mock build, keyboard-only.

### Task 5: TV
- Guide: Select on a programme opens the same three choices; a Recordings destination in the rail with Upcoming and Recorded rows.
- Unit tests for the view models.

### Task 6: Pause and rewind live TV
- The rolling buffer integration already exists and is disabled. Document the one host step it needs (a dedicated, size-capped filesystem mounted at `DVR_DIR`) in `docs/DVR_INTEGRATION.md` and expose its status on the admin status screen. Enabling it is a deployment action, not code.

### Verification
- [ ] On the dev instance: record ten minutes of a live channel while watching it (source shows one connection), record another channel with nobody watching (two connections), play both back in a browser, and confirm the files land with the expected names.
