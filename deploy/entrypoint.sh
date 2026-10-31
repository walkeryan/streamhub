#!/bin/sh
# StreamHub container entrypoint.
#
# Runs as root only long enough to:
#   1. remap the app user/group to PUID/PGID,
#   2. grant the app user access to /dev/dri render nodes (VA-API), if present,
#   3. fix ownership of /config and /transcode,
#   4. set the timezone from TZ,
# then drops privileges with gosu and execs the real command (CMD).
# If the container was started with an explicit --user, no remapping is
# attempted and the command runs directly.
set -eu

APP_USER=streamhub
APP_GROUP=streamhub
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

if [ "$(id -u)" -ne 0 ]; then
    exec "$@"
fi

case "$PUID$PGID" in
    ''|*[!0-9]*)
        echo "entrypoint: PUID and PGID must be numeric (got PUID=$PUID PGID=$PGID)" >&2
        exit 1
        ;;
esac

# Remap group and user IDs (-o allows non-unique IDs, e.g. 99/100 on Unraid).
groupmod -o -g "$PGID" "$APP_GROUP"
usermod -o -u "$PUID" -g "$PGID" "$APP_USER"

# VA-API: add the app user to the group that owns the first render node.
for dev in /dev/dri/renderD*; do
    [ -e "$dev" ] || continue
    gid="$(stat -c %g "$dev")"
    if [ "$gid" != "0" ]; then
        name="$(getent group "$gid" | cut -d: -f1 || true)"
        if [ -z "$name" ]; then
            groupadd -o -g "$gid" streamhub-render
            name=streamhub-render
        fi
        usermod -a -G "$name" "$APP_USER"
    fi
    break
done

mkdir -p /config /transcode
for path in /config /transcode; do
    if [ "$(stat -c %u "$path")" != "$PUID" ] || [ "$(stat -c %g "$path")" != "$PGID" ]; then
        chown -R "$PUID:$PGID" "$path"
    fi
done

if [ -n "${TZ:-}" ] && [ -e "/usr/share/zoneinfo/$TZ" ]; then
    ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime
    echo "$TZ" > /etc/timezone
fi

exec gosu "$APP_USER" "$@"
