#!/bin/sh
# media-ops: watchdog and nightly backup for the IPTV engine container.
#
# The engine's own healthcheck only fetches a static page, so the container
# stays "healthy" when its embedded database has died. This loop probes a
# database-backed URL instead and restarts the container after repeated
# failures. All state lives in files under STATE_DIR so one iteration can be
# run and inspected on its own (MEDIA_OPS_ONESHOT=1), which is how the tests
# drive it.
set -u

PROBE_URL=${PROBE_URL:?set PROBE_URL to a database-backed endpoint}
TARGET_CONTAINER=${TARGET_CONTAINER:?set TARGET_CONTAINER}
PROBE_INTERVAL=${PROBE_INTERVAL:-60}
FAIL_THRESHOLD=${FAIL_THRESHOLD:-3}
COOLDOWN=${COOLDOWN:-600}
NOTIFY_WEBHOOK=${NOTIFY_WEBHOOK:-}
STATE_DIR=${STATE_DIR:-/state}
BACKUP_HOUR=${BACKUP_HOUR:-9}          # UTC; the stock image has no tzdata
BACKUP_SOURCES=${BACKUP_SOURCES:-}
BACKUP_DEST=${BACKUP_DEST:-}
BACKUP_KEEP=${BACKUP_KEEP:-7}

mkdir -p "$STATE_DIR"
INCIDENTS="$STATE_DIR/incidents.jsonl"

now() { echo "${MEDIA_OPS_NOW:-$(date +%s)}"; }
fmt() { date -u -d "@$1" "+$2" 2>/dev/null || date -u -r "$1" "+$2"; }
get() { cat "$STATE_DIR/$1" 2>/dev/null || echo "${2:-0}"; }
put() { printf '%s\n' "$2" > "$STATE_DIR/$1"; }
esc() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr '\n' ' '; }

incident() { # event detail
    line=$(printf '{"at":"%s","event":"%s","target":"%s","detail":"%s"}' \
        "$(fmt "$(now)" %Y-%m-%dT%H:%M:%SZ)" "$1" "$(esc "$TARGET_CONTAINER")" "$(esc "$2")")
    printf '%s\n' "$line" >> "$INCIDENTS"
    if [ -n "$NOTIFY_WEBHOOK" ]; then
        wget -q -T 10 -O /dev/null --header 'Content-Type: application/json' \
            --post-data "$(printf '{"text":"media-ops: %s %s (%s)"}' "$1" "$(esc "$TARGET_CONTAINER")" "$(esc "$2")")" \
            "$NOTIFY_WEBHOOK" || true
    fi
}

probe() { wget -q -T 15 -O /dev/null "$PROBE_URL"; }

watchdog_tick() {
    t=$(now)
    if probe; then
        since=$(get down_since 0)
        if [ "$since" != "0" ]; then
            incident recovered "down for $((t - since))s"
            put down_since 0
        fi
        put failures 0
        return 0
    fi
    n=$(( $(get failures 0) + 1 ))
    put failures "$n"
    [ "$(get down_since 0)" = "0" ] && put down_since "$t"
    [ "$n" -lt "$FAIL_THRESHOLD" ] && return 0
    last=$(get last_restart 0)
    [ $((t - last)) -lt "$COOLDOWN" ] && return 0
    put last_restart "$t"
    put failures 0
    if docker restart "$TARGET_CONTAINER" >/dev/null 2>&1; then
        incident restart "$n consecutive probe failures"
    else
        incident restart_failed "$n consecutive probe failures; docker restart returned an error"
    fi
}

backup_tick() {
    [ -n "$BACKUP_DEST" ] && [ -n "$BACKUP_SOURCES" ] || return 0
    t=$(now)
    day=$(fmt "$t" %Y-%m-%d)
    [ "$(fmt "$t" %H | sed 's/^0//')" = "$BACKUP_HOUR" ] || return 0
    [ "$(get last_backup_day none)" = "$day" ] && return 0
    dest="$BACKUP_DEST/$day"
    mkdir -p "$dest" || { incident backup_failed "cannot create $dest"; return 0; }
    copied=0
    for src in $BACKUP_SOURCES; do
        if [ -d "$src" ]; then
            newest=$(ls -t "$src" 2>/dev/null | head -n 1)
            [ -n "$newest" ] && cp -p "$src/$newest" "$dest/" && copied=$((copied + 1))
        elif [ -f "$src" ]; then
            # SQLite in WAL mode: the -wal and -shm files are part of the database.
            for f in "$src" "$src-wal" "$src-shm"; do
                [ -f "$f" ] && cp -p "$f" "$dest/" && copied=$((copied + 1))
            done
        fi
    done
    put last_backup_day "$day"
    # Keep the newest BACKUP_KEEP dated folders.
    ls -1d "$BACKUP_DEST"/20??-??-?? 2>/dev/null | sort | head -n "-$BACKUP_KEEP" | while read -r old; do
        rm -rf "$old"
    done
    incident backup "$copied files to $dest"
}

tick() { watchdog_tick; backup_tick; }

if [ "${MEDIA_OPS_ONESHOT:-0}" = "1" ]; then
    tick
    exit 0
fi
echo "media-ops: watching $TARGET_CONTAINER via $PROBE_URL every ${PROBE_INTERVAL}s"
while :; do
    tick
    sleep "$PROBE_INTERVAL"
done
