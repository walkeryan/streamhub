#!/bin/sh
# Tests for media-ops.sh. Fakes `wget` and `docker` on PATH and steps the loop
# one iteration at a time with a controlled clock.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/state" "$T/src/engine" "$T/dest"
cat > "$T/bin/wget" <<'W'
#!/bin/sh
echo "$*" >> "$FAKE/wget.log"
case "$*" in *post-data*) exit 0;; esac
exit "$(cat "$FAKE/probe_rc")"
W
cat > "$T/bin/docker" <<'D'
#!/bin/sh
echo "$*" >> "$FAKE/docker.log"
D
chmod +x "$T/bin/wget" "$T/bin/docker"
: > "$T/docker.log"; : > "$T/wget.log"
fails=0
ok()   { echo "ok   - $1"; }
bad()  { echo "FAIL - $1"; fails=$((fails + 1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }
step() { # now [extra env...]
    n=$1; shift
    env PATH="$T/bin:$PATH" FAKE="$T" STATE_DIR="$T/state" MEDIA_OPS_ONESHOT=1 MEDIA_OPS_NOW="$n" \
        PROBE_URL=http://engine/probe TARGET_CONTAINER=engine FAIL_THRESHOLD=3 COOLDOWN=600 "$@" \
        sh "$HERE/media-ops.sh"
}
restarts() { grep -c '^restart engine$' "$T/docker.log"; }

echo 1 > "$T/probe_rc"
step 1000; step 1060
check "two failures do not restart" '[ "$(restarts)" = 0 ]'
step 1120
check "third failure restarts once" '[ "$(restarts)" = 1 ]'
step 1180; step 1240; step 1300
check "failures inside the cooldown do not restart again" '[ "$(restarts)" = 1 ]'
echo 0 > "$T/probe_rc"
step 1360
check "recovery is recorded with the outage length" 'grep -q "\"event\":\"recovered\".*down for 360s" "$T/state/incidents.jsonl"'
echo 1 > "$T/probe_rc"
step 1420; step 1480
check "a success resets the failure counter" '[ "$(restarts)" = 1 ]'
step 2000
check "after the cooldown a new outage restarts again" '[ "$(restarts)" = 2 ]'
check "every incident line is valid JSON" 'python3 -c "import json,sys; [json.loads(l) for l in open(sys.argv[1])]" "$T/state/incidents.jsonl"'
step 2060 NOTIFY_WEBHOOK=http://hook/x >/dev/null; step 2120 NOTIFY_WEBHOOK=http://hook/x; step 2700 NOTIFY_WEBHOOK=http://hook/x
check "the webhook is called on a restart" 'grep -q "post-data.*restart engine.*http://hook/x" "$T/wget.log"'

# Backups. 1794214800 = 2026-11-09 09:00:00 UTC.
echo 0 > "$T/probe_rc"
echo old > "$T/src/engine/a.zip"; sleep 1; echo new > "$T/src/engine/b.zip"
echo db > "$T/src/hub.db"; echo wal > "$T/src/hub.db-wal"
B="BACKUP_DEST=$T/dest BACKUP_HOUR=9 BACKUP_KEEP=7"
day0=1794214800
step $((day0 - 3600)) $B BACKUP_SOURCES="$T/src/engine $T/src/hub.db"
check "no backup outside the backup hour" '[ -z "$(ls "$T/dest")" ]'
step "$day0" $B BACKUP_SOURCES="$T/src/engine $T/src/hub.db"
check "backup copies the newest engine archive" '[ "$(cat "$T/dest/2026-11-09/b.zip")" = new ] && [ ! -e "$T/dest/2026-11-09/a.zip" ]'
check "backup copies the database with its WAL" '[ -f "$T/dest/2026-11-09/hub.db" ] && [ -f "$T/dest/2026-11-09/hub.db-wal" ]'
step $((day0 + 60)) $B BACKUP_SOURCES="$T/src/engine $T/src/hub.db"
check "only one backup per day" '[ "$(grep -c "\"event\":\"backup\"" "$T/state/incidents.jsonl")" = 1 ]'
i=1; while [ $i -le 7 ]; do step $((day0 + i * 86400)) $B BACKUP_SOURCES="$T/src/engine $T/src/hub.db"; i=$((i + 1)); done
check "the eighth day prunes the oldest folder" '[ "$(ls "$T/dest" | wc -l)" = 7 ] && [ ! -e "$T/dest/2026-11-09" ]'

[ "$fails" = 0 ] && echo "all passed" || { echo "$fails failed"; exit 1; }
