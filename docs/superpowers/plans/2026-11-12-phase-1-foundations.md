# Phase 1: Foundations — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the host safe to build on: free appdata space, self-heal and back up the IPTV engine, and run a dev instance of this branch beside the live one.

**Architecture:** Ops tooling lives in `deploy/ops/` and `deploy/dev/` as plain scripts and compose files driven by environment variables, so nothing host-specific is committed. One small sidecar container (`media-ops`, stock `docker:cli` image, no build) runs the watchdog loop and the nightly backup. The dev instance reuses the published image with the branch's source bind-mounted.

**Tech Stack:** POSIX sh, Docker Compose, the published `ghcr.io/walkeryan/streamhub` image, uv (Python 3.12), Node.

## Global Constraints

- No image builds on the host (the Docker socket proxy forbids them): stock images plus bind mounts only.
- No secrets, LAN addresses, provider names or personal names in the repository; all of those come from `.env` files on the host.
- Never restart or recreate containers this project did not create, except the IPTV engine container via the watchdog.
- The live StreamHub container is not touched in this phase.

---

### Task 1: Free appdata space

**Files:** none (host operation; record result in the PR description).

- [ ] Confirm Jellyfin's `MetadataPath` is the array mount and that no file under the old in-config metadata directory is newer than the move date.
- [ ] Delete the old in-config metadata directory (about 21 GB).
- [ ] Delete StreamHub's `pre-catalog-*.db*` migration backups older than seven days (about 2 GB).
- [ ] Record appdata free space before and after.

### Task 2: Ops sidecar — watchdog

**Files:**
- Create: `deploy/ops/media-ops.sh` — the loop: probe, count failures, restart, notify, nightly backup.
- Create: `deploy/ops/docker-compose.yml` — one service, `docker:cli` image, script bind-mounted read-only.
- Create: `deploy/ops/.env.example` — every variable with a neutral example value.
- Create: `deploy/ops/README.md` — what it does, variables, how to test.
- Test: `deploy/ops/test-media-ops.sh` — runs the script's functions against a fake `docker` and `wget` on `PATH`.

**Interfaces (environment):**
`PROBE_URL` (a database-backed endpoint of the engine), `TARGET_CONTAINER`, `PROBE_INTERVAL` (default 60), `FAIL_THRESHOLD` (default 3), `COOLDOWN` (default 600, minimum seconds between restarts), `NOTIFY_WEBHOOK` (optional; receives JSON `{"text": ...}`), `STATE_DIR` (default `/state`; holds `incidents.jsonl`), `DOCKER_HOST`.

**Behavior:** every `PROBE_INTERVAL` fetch `PROBE_URL` with a 15 s timeout. HTTP 200 resets the failure count. On `FAIL_THRESHOLD` consecutive failures, and if `COOLDOWN` has elapsed since the last restart: `docker restart $TARGET_CONTAINER`, append `{"at","event":"restart","failures","detail"}` to `incidents.jsonl`, post to the webhook if set. When the probe recovers, append a `recovered` incident with the outage duration.

- [ ] Write `test-media-ops.sh`: (a) three failing probes trigger exactly one restart call; (b) a fourth failure inside the cooldown does not; (c) a success resets the counter; (d) incident lines are valid JSON.
- [ ] Run it; expect failure (script missing).
- [ ] Implement `media-ops.sh` with a `MEDIA_OPS_ONESHOT` hook so the test can step the loop.
- [ ] Run the test; expect all four cases to pass.
- [ ] Deploy to the host's appdata, start it, and verify end to end once: stop the engine's database inside its container, confirm a restart within `FAIL_THRESHOLD × PROBE_INTERVAL` plus 60 s, and that both incidents are recorded. Check for active streams first.
- [ ] Commit.

### Task 3: Ops sidecar — nightly backup

**Files:** Modify `deploy/ops/media-ops.sh`, `docker-compose.yml`, `.env.example`, `README.md`, `test-media-ops.sh`.

**Interfaces (environment):** `BACKUP_HOUR` (default 4, local time), `BACKUP_SOURCES` (space-separated container-side directories or files, mounted read-only), `BACKUP_DEST` (array path mounted read-write), `BACKUP_KEEP` (default 7).

**Behavior:** once per day at `BACKUP_HOUR`, copy the newest file of each source directory (the engine writes its own nightly archive) and a consistent copy of each named SQLite file (`sqlite3 .backup` via a throwaway container is not available in `docker:cli`; copy the `.db` together with `-wal`/`-shm` into a dated folder) to `BACKUP_DEST/YYYY-MM-DD/`, then delete dated folders beyond `BACKUP_KEEP`. Append a `backup` incident with byte counts.

- [ ] Extend the test: a fake clock at `BACKUP_HOUR` produces one dated folder; an eighth day prunes the oldest.
- [ ] Implement; run tests; deploy; force one run and list the destination.
- [ ] Commit.

### Task 4: Dev instance

**Files:**
- Create: `deploy/dev/docker-compose.dev.yml` — published image, `hub-server/app` mounted over `/app/app`, `web-app/dist` over `/app/web-dist`, `uvicorn --reload`, port from `DEV_PORT`.
- Create: `deploy/dev/.env.example`, `deploy/dev/README.md`.

- [ ] Run the server test suite on `main`'s code with `uv run --python 3.12` to record the baseline.
- [ ] Build the web app (`npm ci && npm run build`).
- [ ] Start the dev instance with its own config directory; confirm `/api/v1/health` and the web shell load over the LAN.
- [ ] Commit, push the branch, open the draft pull request.
