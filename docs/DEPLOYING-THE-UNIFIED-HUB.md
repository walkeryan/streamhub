# Deploying the unified hub release

This release turns StreamHub into the single front end for an IPTV manager (through its Xtream-compatible API)
and a library server (Plex or Jellyfin), with users and roles, watch rooms, recording and requests. This page is
the upgrade checklist for an existing installation. Design: `docs/superpowers/specs/2026-11-12-unified-hub-design.md`.

## Before you upgrade

1. Copy the `/config` directory somewhere safe. The database upgrades in place (revisions 0007–0013) and cannot be
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

## Putting it on the internet

StreamHub is built to be reached from outside the home through a reverse proxy or a tunnel that terminates
HTTPS. Do not publish port 8787 itself. Work through this section before the first outside request arrives;
most of it is about one thing, which is the server knowing where a request really comes from.

### The reverse proxy or tunnel

- HTTPS ends at the proxy (Caddy, nginx, Traefik) or at the tunnel's edge (a Cloudflare Tunnel, Tailscale
  Funnel and the like). The proxy forwards plain HTTP to the container on a network only it can reach.
- The proxy must pass WebSockets (watch rooms) and must not buffer responses (HLS, live streams).
- The proxy must send `X-Forwarded-For` with the client's address appended, and `X-Forwarded-Proto`. Caddy,
  Traefik and tunnel connectors do. nginx does only if told to (`proxy_set_header X-Forwarded-For
  $proxy_add_x_forwarded_for; proxy_set_header X-Forwarded-Proto $scheme; proxy_set_header Host $host;`).
  A proxy that forwards without saying for whom makes every visitor look like the proxy.

### `FORWARDED_ALLOW_IPS`: who is believed about the client's address

Set it to the address or subnet the proxy or tunnel connector connects **from**, as the container sees it:
`FORWARDED_ALLOW_IPS=172.18.0.5`, or the Docker network the connector is on, `172.18.0.0/16`. The default is
`127.0.0.1`, which is right only for a proxy in the same network namespace.

- Never `*`. It means every client is believed about its own address; the server warns at startup when it sees it.
- If the proxy is not listed, the server fails closed: a request that carries a forwarding header from an
  unlisted peer is treated as coming from outside the home, whatever the header says. Nobody is let in by
  that mistake, but people at home lose the home-network conveniences below, and every visitor shares one
  rate-limit allowance. If people at home are told they are "only on the home network" while at home, this
  setting is wrong.
- The same list decides whose `X-Forwarded-Proto` and `X-Forwarded-Host` are believed, which is what makes
  links, redirects and `Strict-Transport-Security` come out right.

### `home_networks`: what counts as home

Settings → `home_networks` (a `system.manage` setting; `PATCH /settings`) lists the networks that are the
household's own. It controls three things: a user **without** `remote.access` is served only from there; the
profile picker (profile tiles, PIN sign-in) is offered only there; and a profile with no PIN can be entered by
choosing it only there.

The default is every private range, which includes the Docker networks your proxy and other containers live
on. Once the server is public, narrow it to the real LAN, for example `192.168.1.0/24` (plus your VPN's range
if people at home use one). Then an address that merely happens to be private is not home.

### Ways to sign in from outside

- **Google.** Needs `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and
  `GOOGLE_REDIRECT_URI=https://<your public host>/api/v1/auth/google/callback`. Add exactly that URI to the
  OAuth client's authorized redirect URIs in the Google Cloud console. Set `GOOGLE_OWNER_EMAIL` so the owner
  claims the Owner user on first sign-in.
- **Plex.** Needs nothing registered anywhere: the browser signs in on Plex's own page and comes back. Works
  on any host name the server is reached by. The owner signs in with Plex by linking it first: Settings →
  Profile → Sign-in methods → Link Plex. (Signing in with an unlinked Plex account makes a new person who waits
  for approval; it never becomes the owner.)
- **Username and password.** For people with neither. Users → Add person gives a one-time setup link; the
  person chooses a username and a password. There is no self-registration and no e-mail recovery: Users →
  the person → Reset password gives a new link.
- **Recommended:** `signup_mode=invite_only` (Users → Invites, or `PATCH /settings`). In the default
  `approval` mode anyone with a Google or Plex account can put themselves in your approval queue. With
  invite-only, a stranger is refused and nothing is created. `plex_friends_auto_approve` (off by default)
  lets an account your Plex server is already shared with in as a Friend without an invite.
- Switch off what you do not use: `plex_sign_in` and `password_sign_in` in settings.
- `GOOGLE_AUTH_REQUIRED=true` closes the household profile picker everywhere, the home network included.
  Leave it unset if people at home pick a profile on the TV; set it if every person should sign in for
  themselves. It does not switch Plex or password sign-in off.
- A TV away from home pairs through a signed-in browser (More → Connect a TV); it needs nothing else.

### Other settings to check

| Setting | For a public host |
|---|---|
| `SECRET_KEY` | At least 32 characters (`openssl rand -hex 32`); a shorter one stops the server from starting. An existing install with a shorter key either re-enters its source credentials after changing it, or sets `SECRET_KEY_ALLOW_SHORT=true` to keep it. |
| `CORS_ORIGINS` | Leave empty. The web app is served from the server's own origin; list another origin only if you really host a client there. |
| `API_DOCS_ENABLED` | Leave unset. `/api/docs` and `/api/openapi.json` are off unless it is `true`. |
| `proxy_mode` (Settings → Playback) | Keep `auto` or `always`. Whatever it says, a viewer who does not manage sources is always played through this server, so no viewer is handed a source's address or credentials; `never` now only affects whoever manages sources. Make sure the server has the upload bandwidth for the people you invite. |
| `ADMIN_PASSWORD` | Only signs the bootstrap admin profile in from the home network. Give the owner a way in from outside (Google, or link Plex or set a password under Sign-in methods) before relying on remote access. |
| Roles | `remote.access` is what lets a role be used away from home. Friend and Family have it; Kid does not. |

### Checklist

1. `FORWARDED_ALLOW_IPS` names the proxy or connector, not `*`; the startup log has no warning about it.
2. From a phone on mobile data, signed out: the sign-in page shows Google / Plex / username and password and
   **no profile tiles**; `https://<host>/api/v1/profiles` answers 404; `https://<host>/api/docs` answers 404.
3. From a device on the home Wi-Fi: profile tiles still appear (if you use them).
4. `home_networks` is your LAN, not every private range.
5. `signup_mode` is `invite_only`, or you are content to see strangers in the approval queue.
6. The owner can sign in from outside without the admin password.
7. A Kid user (no `remote.access`) is refused from mobile data and served at home.
8. Response headers on the public host include `Strict-Transport-Security`, `X-Frame-Options: DENY` and a
   `Content-Security-Policy`; a film still plays and a watch room still connects.
9. Devices that were signed in before the upgrade keep working without signing in again.

## Checks

- A film that no browser can decode plays in a browser, with an audio-track choice, and the manager shows one
  connection while it plays and while seeking.
- A Kid user cannot find or open a title above their ceiling, even by direct API call.
- Two people in a watch room on a limited source use one connection.
- A recording scheduled from the guide lands in `RECORDINGS_DIR` and plays back.
- From outside the home network, a user without `remote.access` is refused.
