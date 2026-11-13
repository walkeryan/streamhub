# Deploying the unified hub release

This release turns StreamHub into the single front end for an IPTV manager (through its Xtream-compatible API)
and a library server (Plex or Jellyfin), with users and roles, watch rooms, recording and requests. This page is
the upgrade checklist for an existing installation. Design: `docs/superpowers/specs/2026-11-12-unified-hub-design.md`.

## Before you upgrade

1. Copy the `/config` directory somewhere safe. The database upgrades in place (revisions 0007–0011) and cannot be
   downgraded.
2. Decide who the owner is. Set `GOOGLE_OWNER_EMAIL` to that Google address. The old `GOOGLE_ALLOWED_EMAILS`
   allowlist is no longer consulted except as a fallback for this bootstrap.
3. If the server sits behind a reverse proxy or tunnel, put the proxy's address or network in
   `FORWARDED_ALLOW_IPS`. Without it every client appears local and the `remote.access` permission cannot be
   enforced.

## Container changes

| What | How |
|---|---|
| Hardware transcoding (browsers that cannot decode a file get HLS) | Give the container the GPU (`runtime: nvidia` plus `NVIDIA_VISIBLE_DEVICES`, or `/dev/dri` for VA-API), then Settings → Playback → hardware `nvenc`, `vaapi` or `qsv`. Software encoding is the fallback. |
| Recording | Mount a directory on the library disk and set `RECORDINGS_DIR` to it; `RECORDINGS_MIN_FREE_GB` (default 50) is the floor at which captures stop. Pointing a library server's recordings library at the same directory makes recordings appear there too. |
| Pause and rewind of live TV | Optional. Needs a dedicated, size-capped filesystem; see `docs/DVR_INTEGRATION.md`. |
| Artwork cache | `IMAGE_CACHE_MAX_MB` (default 512) under `/config/cache/images`. |
| TV app updates | Put the signed APK and a `tv-release.json` manifest in the directory mounted at `/app/web-dist/downloads`; see "TV app release manifest" in `hub-server/README.md`. |

## After the first start

1. Sign in as the owner. Existing profiles have become users: the admin profile is the Owner, kids profiles are
   Kid, the rest Family. Review them under **More → Users**.
2. **Sources.** Remove any source that talks to an IPTV provider directly and add the IPTV manager instead
   (type Xtream, the manager's URL and a user created for StreamHub there). Recommended source options:
   `proxy: "always"` (clients never see the manager), `max_connections` (what the provider allows),
   `default_language`, `adult_categories`, `hidden_groups`, and `shelf_map` / `group_map` for names the built-in
   cleanup does not get right. Then run a sync.
3. **Library server.** Link it from Sources (Plex uses a PIN link; StreamHub gets its own token and never needs
   the server's). Set its `default_language`. To sync resume points with Plex, turn on "Link to Plex account" on
   the one user who owns that Plex account.
4. **Requests.** Settings → Playback → request manager URL and API key (Overseerr, Jellyseerr or Seerr).
5. **People.** Choose approval or invite-only sign-up (Users → Invites). Approve people as they sign in, add
   managed users for children, set PINs for anyone with wider access than a child, and mark the living-room TV as
   a shared household device when pairing it.
6. **Ratings.** With a TMDB key set, a background job classifies titles so rating ceilings work without waiting
   for first view. A new installation needs a while to work through a large catalog.

## Checks

- A film that no browser can decode plays in a browser, with an audio-track choice, and the manager shows one
  connection while it plays and while seeking.
- A Kid user cannot find or open a title above their ceiling, even by direct API call.
- Two people in a watch room on a limited source use one connection.
- A recording scheduled from the guide lands in `RECORDINGS_DIR` and plays back.
- From outside the home network, a user without `remote.access` is refused.
