# media-ops

A small sidecar for a StreamHub host that also runs an all-in-one IPTV engine container.

**Watchdog.** The engine's built-in healthcheck fetches a static page, so the container
reports healthy even when its embedded database has stopped. `media-ops` probes a
database-backed URL every `PROBE_INTERVAL` seconds. After `FAIL_THRESHOLD` consecutive
failures it restarts `TARGET_CONTAINER`, at most once per `COOLDOWN` seconds. Restarts
and recoveries are appended to `state/incidents.jsonl` and, if `NOTIFY_WEBHOOK` is set,
posted there as `{"text": "..."}`.

**Backup.** Once a day at `BACKUP_HOUR` (UTC) it copies the newest file from each source
directory and each named SQLite database (with its `-wal` and `-shm` files) into a dated
folder under the destination, keeping the newest `BACKUP_KEEP` folders.

It needs Docker API access limited to restarting containers; point `DOCKER_HOST` at a
socket proxy rather than mounting the socket.

```bash
cp .env.example .env    # then edit
docker compose up -d
sh test-media-ops.sh    # unit tests; no Docker needed
```
