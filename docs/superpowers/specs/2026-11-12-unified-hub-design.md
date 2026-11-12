# StreamHub as the one front door: design

**Date:** 2026-11-12 · **Status:** awaiting owner review · **Scope:** one release, eight phases

## 1. Goal

One simple interface, the same everywhere, that shows everything the household can watch:

- every movie and show in the owned library (served by Plex),
- every English-language movie, series and live channel the IPTV provider offers,
- organized into proper categories, with the organizing done in Dispatcharr,
- fully usable with a four-way remote plus Select, Back and Home,
- on Android TV (native app), and on phone, tablet and a console browser (web app),
- for the household and for invited friends, including watching together.

"Done" for the release means the family and friends have moved to StreamHub and the
Jellyfin stack that currently fills this role has been switched off.

### Acceptance scenarios

1. On the TV, with the remote only: search "The Mill", open the 2023 film, pick an audio
   track, play. No dead ends; Back returns to the search results with focus restored.
2. The same title on a phone, away from home, signed in with Google.
3. A friend opens an invite link in a console browser and watches that film in sync with
   the owner; pause and seek carry to both screens; only one provider stream is used.
4. On the shared TV each family member picks themselves and gets their own Continue
   Watching. A kid cannot see, search for, or play adult-category or over-rating titles,
   even by calling the API directly, and cannot switch to a parent without the PIN.
5. From the guide, "record this" and "record every game for this team" produce files that
   play in StreamHub and also appear in Plex.
6. A title in neither source can be requested from the search screen and shows up, with a
   notice to the requester, once it has been downloaded.
7. A film started in the Plex phone app resumes at the same point in StreamHub on the TV.
8. Someone new signs in with Google and sees "waiting for approval". The owner opens
   Users, approves them as Friend, and they are in without signing in again. The owner
   later suspends them and their TV stops playing within a minute.

## 2. Architecture

Three layers, each with one job.

```
                 ┌──────────────────────── StreamHub server ────────────────────────┐
 TV app  ──────► │ catalog merge · users+policy · playback resolver · stream        │
 web app ──────► │ proxy (remux/NVENC) · rooms · recorder · requests                │
                 └──────┬───────────────────────────┬───────────────────────┬───────┘
                        │ Xtream API (one user)     │ Plex API              │ Seerr API
                        ▼                           ▼                       ▼
                   Dispatcharr                     Plex                Radarr / Sonarr
              (IPTV engine + organizer)     (owned-library server)      (downloads)
                        │
              provider lines · antenna tuner
```

- **Dispatcharr is the IPTV engine and the organizer.** It owns the provider lines,
  failover, the guide, and every decision about what exists and which category it is in.
  Nothing else talks to the provider. StreamHub's present direct provider source is
  removed; it competes with Dispatcharr for the same connection limit.
- **Plex stays a normal, working Plex server.** It scans and serves the owned library.
  Its own apps keep working (the owner uses the phone app occasionally) and its existing
  IPTV live tuner stays as it is. Nobody is required to open a Plex app.
- **StreamHub is the only interface people need.** The server merges both catalogs,
  enforces users and policy, resolves playback, and hosts the new services. The two
  clients stay thin: they render what the API returns and play a `PlaybackDescriptor`.

Why this shape and not "StreamHub as a skin over Jellyfin": Jellyfin flattens
Dispatcharr's categories into libraries, hides audio tracks of stream-link titles until
playback, and brought repeated database lock-ups at this catalog size. Building on
Dispatcharr directly keeps the category model in one place and ends with fewer moving
parts than today.

### What already exists in StreamHub (reused, not rebuilt)

Xtream, Plex, M3U and XMLTV adapters · TMDB-keyed titles with stream variants and
audio-track choice · profiles, profile PINs, owner PIN · a server-side content policy
service (disabled by default) · owner-only Google sign-in and TV device pairing ·
live TV, guide, favorites, watchlist, history · stream proxy with codec-copy HLS, libx264,
VAAPI and NVENC paths and a shared upstream per channel · provider-archive rewind · a
rolling-buffer DVR core (present, switched off) · sports classification · 66 server test
modules, native TV tests, Playwright suites, CI that publishes the image.

### What is new

A Dispatcharr source mode (Xtream against Dispatcharr, with category mapping) · catalog
merge across Plex and IPTV · two-way Plex progress · users, roles and permissions with a
management screen · watch rooms · recorder · requests · a status/watchdog surface.

## 3. Catalog

**In Dispatcharr**

- Enable every English-language movie and series category on the VOD-capable line. Today
  29 English movie categories and 3 series categories are still off. Rule: categories
  prefixed `EN -` / `ENGLISH`, plus the unprefixed streaming-brand categories; region-
  labelled ones (other country prefixes, "ASIA", "MENA") stay off. The adult category is
  enabled and gated by policy (section 6), never by omission.
- One Dispatcharr user for StreamHub, streamer level, with an Xtream password, attached to
  a channel profile named `StreamHub` (seeded from the current curated live lineup).
- Dispatcharr remains the place to enable, disable and group. StreamHub never writes to it.

**In StreamHub**

- The Xtream adapter gains a `dispatcharr` flavor: same protocol, but it trusts
  Dispatcharr's TMDB ids, treats quality-suffixed categories (4K, Dolby, HEVC) as
  *versions* of a title rather than separate shelves, and maps raw category names to
  display shelves through a small editable table (`NETFLIX MOVIES`, `NETFLIX MOVIES 4K` →
  shelf "Netflix"; `EN - NEW RELEASE` → "New releases"). Unmapped categories appear under
  their cleaned-up raw name, so nothing is ever hidden by a missing mapping.
- **Merge.** Titles are keyed by TMDB id. A title present in both sources is shown once.
  Variant order: owned copy first, then IPTV versions by resolution. The detail page lists
  every version and every audio track.
- **Sync.** Full sync nightly and on demand; incremental by Dispatcharr's `added`
  timestamps in between. Expected size after enabling: tens of thousands of movies and
  several thousand series, within what the existing paged catalog and FTS search handle.
- **Progress.** StreamHub already forwards progress to Plex for owned titles. It now also
  reads Plex's view offsets on sync and on title open, last-writer-wins by timestamp, for
  users linked to a Plex home user (section 5).
- **Existing data.** Existing profiles become users and keep their state; settings are
  kept. The old direct-provider source and
  its variants are dropped; title-level favorites, watchlist and history survive because
  they are keyed by TMDB id. Channel favorites are not migrated.

**Connection budget.** The provider allows two concurrent streams. Owned-library playback
never counts. When both are in use, the player shows who is using them instead of a
generic error; the owner can stop a stream from Settings (existing recovery screen).

## 4. Clients

**Android TV app (the primary client).** The earlier design review scored 4 pass, 5
partial, 1 fail, and sustained real-device playback was never completed. This release
closes that out on the owner's actual TV, driven over wireless ADB:

- safe-area fix for the navigation rail; Back never dead-ends and always restores focus;
  player controls reachable by D-pad; search keeps its query on Back;
- every screen passes a scripted remote-only walk (arrows, Select, Back, Home), captured
  as screenshots in CI artifacts for the emulator and by ADB for the real device;
- a two-hour playback soak each for an IPTV film, an owned film and a live channel;
- the app keeps its signing key so updates install over the top; the server hosts the
  current APK and the app shows "update available".

**Web app (phone, tablet, console browser).** Installable as a PWA. Touch layouts for
phone and tablet; the existing spatial navigation serves gamepad and keyboard. Browsers
that cannot decode a stream get an HLS remux, or an NVENC transcode when the codec
requires it. The GPU is shared with Plex, so concurrent transcodes are capped and the
cap is surfaced in the status screen.

**Not built:** native phone apps, a native console app.

## 5. Users, roles and permissions

Today Google sign-in is owner-only: every allowed address maps to the single admin
profile, and there is no user record at all. This release adds one.

**Model: one person, one user**

- **User** = one person. Each user has their own watch history, resume points, favorites,
  watchlist, preferences, role, permissions and limits. Nothing is shared between users.
- A user signs in one of two ways. A **Google user** has a Google identity and can sign in
  anywhere. A **managed user** has no Google account (a child, typically); the owner
  creates them, and they exist only on the household's shared devices.
- Every user has a status (`pending`, `active`, `suspended`), one role, and optional
  per-user overrides. The existing profile record becomes the user's viewer state, one to
  one; "profile" stops being a separate thing people manage.
- **Role** = a named set of permissions. Four are built in. Owner is fixed; Family, Kid
  and Friend are editable, and the owner can create more.
- **Permissions** are the unit the server checks. A user's effective set is their role's
  set plus or minus their overrides.

| Permission | Owner | Family | Kid | Friend |
|---|---|---|---|---|
| `watch.library` owned titles | yes | yes | yes | yes |
| `watch.vod` IPTV movies and series | yes | yes | yes | yes |
| `watch.live` live TV and guide | yes | yes | yes | yes |
| `watch.adult` adult category (still PIN-gated) | yes | no | no | no |
| `rooms.join` | yes | yes | yes | yes |
| `rooms.create` | yes | yes | no | yes |
| `recordings.view` | yes | yes | yes | no |
| `recordings.schedule` | yes | yes | no | no |
| `requests.create` | yes | yes | yes | yes |
| `requests.auto_approve` | yes | yes | no | no |
| `requests.approve` other people's requests | yes | no | no | no |
| `remote.access` use away from the home network | yes | yes | no | yes |
| `users.invite` create invite links | yes | yes | no | no |
| `users.manage` · `sources.manage` · `system.manage` | yes | no | no | no |

Limits, set on the role and overridable per user: concurrent streams (Friend and Kid: 1)
and a rating ceiling (Kid: PG by default, unknown ratings hidden).

**Signing in**

- Anyone can sign in with Google. A first sign-in creates a `pending` user who sees a
  "waiting for approval" screen and nothing else; the owner gets a badge on the status
  screen and a webhook notification. A setting switches this to invite-only.
- An **invite link** carries a role and an expiry. Signing in through it activates the
  user at once with that role. Room invitations to people without an account are invite
  links for the Friend role.
- The first owner is bootstrapped from configuration; the existing admin profile and its
  owner PIN migrate to that user. The address allowlist setting is retired.

**Shared devices and "Who's watching?"**

- A device paired by the owner can be marked a **household device** (the living-room TV).
  It opens on a "Who's watching?" picker listing the family's users, Google and managed.
- Picking a user switches the device to that user's history, permissions and limits.
  Each user can have a PIN; switching to a user with wider access than the current one
  always requires that user's PIN. Kids therefore cannot step into a parent's user.
- A personal device (a phone, a friend's browser) is signed in as one user and has no
  picker.

**Users screen** (web app, `users.manage` only)

- Pending queue: approve with a role, or deny.
- Add a managed user: name, avatar, role, optional PIN.
- User list: name, sign-in type, role, status, last seen, what they are watching now.
- User detail: change role; toggle individual permissions; set limits; list devices and
  sessions with revoke; watch history; recent activity; suspend, reinstate, remove.
- Roles tab: edit the permission set and limits of Family, Kid, Friend and custom roles.
- Every change is written to an audit log (who, what, when), shown on the same screen.

**Enforcement**

- Permissions are checked in the server on every request, in one dependency used by all
  routers, and in the catalog query layer for the `watch.*` family. Clients hide what a
  user cannot do but are never the gate.
- Access tokens carry a user version. Changing a role, an override or a status bumps it,
  so the change applies on the next request and a suspended user's streams are cut.

**Plex progress.** Two-way resume (section 3) is per user: a user can be linked to a Plex
home user, and only linked users sync. Unlinked users keep their history in StreamHub.

**Remote access.** The web app and API stay on the existing public hostname with Google
sign-in required. Long-form video should not ride the tunnel: media segments are served
from a direct HTTPS endpoint (reverse proxy plus one forwarded port) when it is
configured, and fall back to the tunnel when it is not. Stream URLs are already
short-lived signed tokens.

## 6. Parental policy

The existing content-policy service is switched on and completed:

- ratings come from TMDB certifications (US), cached on the title;
- a user has a rating ceiling and an "unknown rating" rule from their role or overrides;
  the Kid role hides unknowns;
- the adult category needs the `watch.adult` permission, and opening it asks for that
  user's PIN each session;
- enforcement is in the server query layer, so browse, search, detail, playback resolve,
  rooms and recordings all obey it; switching to a less restricted user needs their PIN.

## 7. Watch rooms

- A room is a server object: host, title + chosen version + audio track, members, and an
  authoritative playback state `{position, playing, as_of}`.
- Create from any title or live channel; share by link, QR code or six-character code.
  Joining needs `rooms.join`; someone without an account gets a Friend invite link.
- Each member holds a WebSocket. Play, pause and seek from any member (host can lock
  controls) update the room state; clients steer to it and correct drift above one second.
- If a member stalls, the room pauses for everyone for up to 15 seconds, then carries on.
- **One upstream per room.** For IPTV titles the proxy opens one provider stream for the
  room and fans it out as HLS to every member, extending the existing shared-upstream
  logic from channels to VOD. Owned titles stream from Plex per member and use no
  provider budget. A room is capped at 8 members.
- Not in scope: chat, voice, reactions.

## 8. Recording

- **Recorder service.** Schedules from guide programmes; codec-copy capture through the
  existing proxy producer, so a recording of a channel someone is watching shares their
  upstream. Padding: 2 minutes early; 5 minutes late, 60 for sports.
- **Who.** Needs `recordings.schedule`; viewing needs `recordings.view`.
- **Rules.** "This programme", "every new episode of this series", and "every game for
  this team" (built on the existing sports classifier and guide titles).
- **Storage.** Files go to the array, in the folder Plex already uses for its recordings
  library, named so Plex matches them. Recordings therefore appear in both apps. Capture
  stops below a free-space floor.
- **Conflicts.** A recording needs a free provider stream unless it can share one. If none
  is free at start time the owner is notified and the recording is retried each minute
  for the first ten; where the channel advertises a provider archive, a missed recording
  is fetched from the archive afterwards.
- **Pause and rewind live TV.** The rolling-buffer core is activated with a capped
  directory on the array (the cache pool is too full to host it).

## 9. Requests

- Search results include TMDB titles that are in neither source, marked "Not available".
  Selecting one offers **Request**.
- StreamHub records the request and its state. Requests from users with
  `requests.auto_approve` go straight through; others wait for someone with
  `requests.approve`. Approved requests are sent to Seerr, which drives Radarr and Sonarr.
- When the title appears in the Plex library sync, the request closes and the requester
  sees a notice on their home screen.

## 10. Operations

- **Watchdog.** Dispatcharr's container reports healthy even when its embedded database
  has stopped (this happened on 2026-11-11 and took live TV down for eight hours). A
  probe hits a database-backed endpoint each minute; three failures restart the container
  record an incident on StreamHub's status screen, and post to a configurable
  notification webhook. StreamHub shows source health on a status screen and degrades to
  "library only" or "IPTV only" instead of failing whole.
- **Backups.** Nightly dump of Dispatcharr's database and StreamHub's SQLite file to the
  array, seven kept.
- **Disk.** The cache pool is at 96%. Phase 1 removes the stale Jellyfin artwork copy and
  old StreamHub pre-migration backups. Phase 8 removes the Jellyfin stack's data.
- **Deploys.** CI publishes the image per merge. The live container is updated at phase
  boundaries; a dev instance runs beside it for validation.

## 11. Delivery

One branch and one draft pull request per phase, off `main`. Each phase gets its own
implementation plan, written just before it starts, so later plans can use what earlier
phases learned. The owner validates each phase on the dev instance and merges. Phases 5,
6 and 7 are independent of one another.

| # | Phase | Ends with |
|---|---|---|
| 1 | Foundations | Space freed, watchdog and backups running, dev instance up |
| 2 | One catalog | Everything browsable and playable from Dispatcharr + Plex on web and TV |
| 3 | TV daily driver | Remote-only walk and soak tests pass on the real TV |
| 4 | People | Users, roles, permissions and the Users screen; parental policy; remote sign-in (scenarios 4 and 8) |
| 5 | Watch rooms | Acceptance scenario 3 |
| 6 | Recording | Acceptance scenario 5, plus pause/rewind live |
| 7 | Requests | Acceptance scenario 6 |
| 8 | Cutover | Antenna tuner in Dispatcharr, friends moved, Jellyfin stack retired |

**Phase 8 detail.** The antenna tuner bridge gains an M3U endpoint so Dispatcharr ingests
it as an account with its own two-tuner limit; its channels get guide data and fail over
to the IPTV affiliates. Then the Jellyfin server, its web skin and bridge, the stream-link
generator and its folder tree, and the Plex-to-Jellyfin history sync are stopped, kept for
two weeks, and deleted.

## 12. Testing

- Server: contract tests against recorded Dispatcharr Xtream responses; merge and policy
  tests over a fixture with overlapping Plex and IPTV titles; room state-machine tests;
  recorder scheduling tests with a fake clock.
- Web: Playwright, keyboard-only and touch, including a two-browser room test asserting
  drift under one second.
- TV: existing native tests, plus the scripted remote walk on emulator and real device.
- Each phase's pull request carries the evidence for its acceptance scenarios.

## 13. Risks

- **Two provider streams** is the tightest constraint in the system. Rooms and shared
  recording upstreams reduce the pressure; adding a third line would remove most of it.
- **GPU transcode sessions** are shared with Plex on an older card.
- **Xtream emulation at scale.** Dispatcharr's VOD listing has not been exercised by
  StreamHub at full English-catalog size; Phase 2 measures it first.
- **App signing key.** Must be located before Phase 3, or TVs need a one-time reinstall.

## 14. Needed from the owner

1. Merge each phase's pull request after validating it.
2. One forwarded port on the router for direct media delivery (Phase 4), or a decision to
   stay on the tunnel.
3. Approving people and picking their role as they sign in (from Phase 4 on).
4. Confirmation of the recordings folder on the array (Phase 6).
