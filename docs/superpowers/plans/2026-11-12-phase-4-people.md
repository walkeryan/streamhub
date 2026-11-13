# Phase 4: People — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One person, one user. People sign in with Google (or are managed users without Google), wait for approval, and get a role whose permissions and limits the server enforces on every request. Shared devices ask "Who's watching?". The owner manages everything from a Users screen.

**Architecture:** A `User` row sits above the existing `Profile` row, one to one; the profile keeps being the token subject and the owner of viewer state, so the many checks keyed on `(profile_id, token_version, sid)` stay valid. A `permissions` module turns role + overrides into a frozen set on the `Principal`; one dependency factory `require(perm)` replaces the `is_admin` checks. A real `auth_session` table makes sessions listable and revocable, and session validation becomes unconditional instead of depending on the legacy content-policy flag. The rating ceiling feeds the existing content-policy engine, which becomes active per user rather than per deployment.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, PyJWT, respx/pytest; React + TanStack Query + MSW + Playwright; Kotlin/Compose for the TV picker.

## Global Constraints

- One person = one user. Nothing is shared between users: history, resume points, favorites, watchlist, preferences, role, permissions, limits.
- The server is the only gate. Clients hide what a user cannot do but every rule is enforced server-side and covered by an API-level test that calls the endpoint directly.
- A change to a user's role, overrides, limits or status applies on that user's next request; suspension cuts running streams within one segment or a few seconds.
- Existing deployments upgrade in place: the Alembic revision converts current profiles into users; no one is locked out by the upgrade.
- The legacy regime behind `CONTENT_POLICY_ENABLED=true` (owner PIN on every unrestricted login, owner re-auth) keeps passing its existing tests when that flag is explicitly on; the new model is what runs when it is off (the default).
- Tests first; `ruff check`, `ruff format --check`, `mypy app`, full `pytest`; web lint, typecheck, build; `contracts/openapi.yaml` updated with `schema.d.ts` regenerated and the contract test green.
- No hostnames, addresses, credentials or personal names in code, tests or docs.

## Permission catalogue (verbatim from the design)

`watch.library`, `watch.vod`, `watch.live`, `watch.adult`, `rooms.join`, `rooms.create`, `recordings.view`, `recordings.schedule`, `requests.create`, `requests.auto_approve`, `requests.approve`, `remote.access`, `users.invite`, `users.manage`, `sources.manage`, `system.manage`.

| Built-in role | Permissions | Limits |
|---|---|---|
| Owner (fixed, cannot be edited or removed; at least one active owner always exists) | all | none |
| Family | all `watch.*` except `watch.adult`; `rooms.*`; `recordings.*`; `requests.create`, `requests.auto_approve`; `remote.access`; `users.invite` | none |
| Kid | `watch.library`, `watch.vod`, `watch.live`; `rooms.join`; `recordings.view`; `requests.create` | 1 stream; rating ceiling movie `PG` / tv `TV-PG`; unknown ratings hidden |
| Friend | `watch.library`, `watch.vod`, `watch.live`; `rooms.join`, `rooms.create`; `requests.create`; `remote.access` | 1 stream |

Limits: `max_streams: int | null`, `rating_ceiling_movie: str | null`, `rating_ceiling_tv: str | null`, `allow_unrated: bool`.

---

### Task 1: Schema and upgrade

**Files:** `hub-server/app/infra/models.py`, new Alembic revision (next free number, chained after the current head), `tests/test_users_migration.py`.

Tables:
- `role`: `id` (slug pk), `name`, `builtin` bool, `permissions` JSON list, `limits` JSON, `on_shared_devices` bool (default: Owner/Family/Kid true, Friend false), `updated_at`.
- `user`: `id` (hex16 pk), `kind` (`google|managed`), `google_subject` (unique, null), `email` (null), `display_name`, `picture` (null), `status` (`pending|active|suspended`), `role_id` (fk role, null while pending), `grants` JSON list, `denials` JSON list, `limits` JSON (overrides), `version` int, `plex_account_linked` bool default false, `created_at`, `approved_at`, `approved_by_user_id`, `last_seen_at`.
- `profile.user_id`: unique fk → `user.id`, cascade.
- `auth_session`: `sid` (pk), `user_id`, `profile_id`, `kind` (`browser|tv|api`), `label`, `user_agent`, `ip`, `shared_device` bool, `device_owner_user_id` (for shared devices: who paired it), `created_at`, `last_seen_at`, `revoked_at`.
- `invite`: `id` (pk), `token_hash` (unique), `role_id`, `created_by_user_id`, `expires_at`, `max_uses` int, `uses` int, `revoked_at`.
- `audit_log`: `id`, `at`, `actor_user_id`, `action`, `target_user_id` (null), `detail` JSON.

Upgrade data step: seed the four built-in roles; for each existing profile create a user: the `is_admin` profile → Owner (`kind=google` with `google_subject` copied from `OwnerCredential.google_subject` when present, else `managed`), `kids` profiles → Kid, the rest → Family; all `active`, `kind=managed`.

- [ ] Test: upgrade a database populated at the previous head (admin + kid + plain profile with favorites/progress) and assert users, roles and untouched viewer state.

### Task 2: Permission engine and Principal

**Files:** create `hub-server/app/services/permissions.py`; modify `services/auth.py` (`Principal`, `_encode`, `resolve_principal`), `api/deps.py`; tests `tests/test_permissions.py`.

```python
PERMISSIONS: frozenset[str]
BUILTIN_ROLES: dict[str, RoleSpec]                     # owner, family, kid, friend
@dataclass(frozen=True)
class Limits: max_streams: int | None; rating_ceiling_movie: str | None; rating_ceiling_tv: str | None; allow_unrated: bool
def effective_permissions(role_permissions: Iterable[str], grants: Iterable[str], denials: Iterable[str]) -> frozenset[str]
def effective_limits(role_limits: Mapping[str, Any], overrides: Mapping[str, Any]) -> Limits
def is_wider(a: frozenset[str], a_limits: Limits, b: frozenset[str], b_limits: Limits) -> bool   # does a exceed b?
```
`Principal` gains `user_id: str`, `permissions: frozenset[str]`, `limits: Limits`, `user_version: int`, `shared_device: bool`; `is_admin` becomes a read-only property equal to `"system.manage" in permissions` so untouched callers keep working. Tokens add `uid` and `uv`. `resolve_principal` always loads user + role (one cached query per request) and rejects when the user is not `active`, when `uv` differs from `user.version`, or when the `auth_session` row is missing or revoked.

`api/deps.py`: `def require(*perms: str) -> Depends` raising 403 `{"code": "forbidden", "missing": [...]}`; `Admin` becomes `require("system.manage")`.

- [ ] Unit tests for permission/limit algebra and `is_wider`.
- [ ] API tests: stale `uv` → 401; suspended → 401 `user_suspended`; pending → 403 `approval_pending` on everything except `GET /auth/me`.

### Task 3: Replace `is_admin` checks

Mapping (apply everywhere the survey listed; grep for `is_admin`, `adm`, `Admin`):
- sources routes, source sync/test, service registry writes → `sources.manage`
- settings writes, TMDB key, themes writes, stream recovery, about/system → `system.manage`
- profile/user create, delete, role/override/limit edits, content-policy edits, PIN resets for others → `users.manage`
- `request_policy.can_request/auto_approve/can_approve` (if present on the branch) → `requests.create` / `requests.auto_approve` / `requests.approve`
- catalog visibility: `watch.library` gates rows from library sources (Plex/Jellyfin), `watch.vod` gates Xtream/M3U VOD, `watch.live` gates channels/EPG/live playback; implemented as predicates in the same query layer the adult predicate uses.
- adult rows: require `watch.adult` **and** an adult unlock on the session (`POST /auth/adult-unlock {pin}` → marks the `auth_session` for 4 hours; needs the user's own PIN, and the user must have a PIN set).
- [ ] One API test per permission proving a user without it gets 403/empty and a user with it succeeds.

### Task 4: Sessions, devices, stream limits

**Files:** `services/auth.py`, new `services/sessions.py`, `services/playback.py`, `proxy/stream_proxy.py`, `api/routers/auth.py`; tests.

- `issue()` creates/updates an `auth_session` row (kind from client hint, label from pairing or UA); `refresh` checks it; `last_seen_at` is touched at most once a minute.
- `GET /auth/sessions` (own; `users.manage` may pass `user_id`), `DELETE /auth/sessions/{sid}`.
- Session validation is unconditional: `require_grant`/`guard_stream`/`require_segment` re-check user status, `uv` and session for every profile, not only when the legacy flag is on.
- `max_streams`: playback resolve counts the user's live grants with recent activity (proxy sessions are tagged with `user_id`); over the limit → 409 `user_stream_limit` naming what is already playing.
- Active streams per user are queryable (`SessionsService.now_playing(user_id)`), which the Users screen and `source_busy` messages use.
- [ ] Tests: revoke → next API call 401 and next segment 401; suspension stops a running proxy stream; stream limit enforced and released on stop.

### Task 5: Sign-in for everyone

**Files:** `services/google_auth.py`, new `services/users.py`, `services/invites.py`, `api/routers/auth.py`, new `api/routers/users.py`; settings `signup_mode` (`approval|invite_only`), `GOOGLE_OWNER_EMAIL` bootstrap (the old allowlist variable is read once as a fallback for bootstrap and otherwise ignored); tests extending `tests/test_google_auth.py`.

- Google callback: verified email required. Lookup by `google_subject`. Unknown subject: if it matches the bootstrap owner email and no Google owner exists → attach to the Owner user; else if an invite token rides the flow → create `active` user with the invite's role; else if `signup_mode=approval` → create `pending` user and notify (`audit_log` + notices for `users.manage` holders + webhook setting `notify_webhook` if set); else reject `invite_required`.
- `/auth/google/complete` returns `{status: "active", tokens…}` or `{status: "pending"}` with a limited token that only allows `GET /auth/me`; the web app polls `/auth/me` and upgrades to full tokens once approved (no second sign-in).
- Invites: `POST /invites {role_id, expires_in_hours, max_uses}` (`users.invite`; non-owners may only invite to roles not wider than their own) → one-time URL `/invite/<token>`; `GET /invites`, `DELETE /invites/{id}`.
- Password/PIN login for non-Google deployments keeps working and maps to the profile's user.
- [ ] Tests: unknown subject → pending → approved → same browser session becomes active; invite path; invite-only rejection; bootstrap owner; a suspended Google user cannot sign in.

### Task 6: Users API

`api/routers/users.py`, `services/users.py`, tests `tests/test_api_users.py`. All `users.manage` unless noted.
- `GET /users?status=` (list with role, status, sign-in kind, last seen, now playing), `GET /users/{id}` (detail incl. effective permissions and limits, devices/sessions, recent history), `POST /users` (managed user: name, avatar, role, optional PIN), `PATCH /users/{id}` (name, avatar, role, grants, denials, limits, `on_shared_devices` override), `POST /users/{id}/approve {role_id}`, `/deny`, `/suspend`, `/reinstate`, `DELETE /users/{id}`.
- `GET /roles`, `POST /roles`, `PATCH /roles/{id}`, `DELETE /roles/{id}` (built-ins cannot be deleted; Owner cannot be edited).
- `GET /audit?user_id=&limit=`.
- Guards: the last active owner cannot be demoted, suspended or deleted; nobody edits their own role or permissions; every mutation bumps `user.version` and writes `audit_log`.
- `GET /auth/me` → user, role, effective permissions, limits, status, `shared_device`.
- [ ] Tests for each guard and each endpoint's permission.

### Task 7: "Who's watching?" on shared devices

- Pairing: the approver may tick "shared household device" (needs `users.manage`); the TV's `auth_session.shared_device=true`, `device_owner_user_id` set. A browser session can be marked shared the same way from Settings.
- `GET /auth/switchable` (shared-device sessions only): active users whose role (or override) has `on_shared_devices`, with `has_pin` and avatar.
- `POST /auth/switch {user_id, pin?}`: allowed only on a shared-device session. A PIN is required when the target has one; a target whose access `is_wider` than the current user's must have a PIN (otherwise 409 `pin_setup_required`). Five failures per 15 minutes per target → 429. Success consumes the old session's profile binding and issues tokens for the target on the same `auth_session` (same device row, new profile/user).
- `PUT /users/{id}/pin` (self, or `users.manage`): 4–8 digits.
- A shared device with no current user (after `POST /auth/switch/leave`) holds a picker-only token.
- [ ] Tests: kid → parent without PIN refused; with PIN allowed; parent → kid needs no PIN; non-shared session refused; lockout.

### Task 8: Rating ceiling through the policy engine

- `ContentPolicy.values(profile)` derives from the user's effective limits: no ceiling and `allow_unrated=true` → unrestricted; otherwise restricted with allowed codes up to the ceiling. The engine is considered active for a request when the user is restricted, independent of the legacy flag; legacy-flag-only behaviors (owner PIN on login, `check_owner_entry`) stay behind the flag.
- Ratings: a background job classifies titles with TMDB ids in batches (existing `classification` service) so restricted browsing is not waiting on first-view lookups; titles from sources that supply an explicit rating use it.
- [ ] Tests: Kid sees PG, not PG-13 or unrated; raising the ceiling for one user changes their results on the next request; an adult user is unaffected; search, detail, resolve, images and stream segments all obey.

### Task 9: Plex resume, both ways

- A user with `plex_account_linked` (settable only for the user whose Plex account owns the configured token; the Users screen offers it on one user) gets Plex `viewOffset`/`lastViewedAt` merged on title open and on sync: newer timestamp wins against local `Progress`. Other users are untouched.
- [ ] Tests with a mocked Plex server: newer Plex offset wins; older loses; unlinked user ignores Plex.

### Task 10: Web

- Sign-in: Google button for everyone; **Waiting for approval** screen that polls `/auth/me`; invite landing `/invite/:token`.
- **Users** screen (rail entry visible with `users.manage`): Pending queue (Approve with role / Deny), Users list (name, sign-in type, role, status, last seen, now playing), user detail (role, per-permission toggles showing inherited vs overridden, limits, devices and sessions with Revoke, history, Suspend / Reinstate / Remove, Set PIN, "Link to Plex account"), Add managed user dialog, Roles tab (edit permissions and limits; create/delete custom roles), Invites tab, Audit tab.
- **Who's watching?** picker on shared-device sessions at launch and from the rail avatar; PIN dialog.
- Settings → Devices: own sessions with Revoke; "This is a shared household device" toggle (`users.manage`).
- All UI gating reads `permissions` from `/auth/me` (a `useCan(perm)` hook); remove direct `is_admin` checks.
- MSW fixtures and Playwright specs: approval flow, role change takes effect, who's-watching with PIN, keyboard-only.

### Task 11: TV

- After pairing and on cold start, a shared device shows **Who's watching?** (users from `/auth/switchable`, D-pad grid, PIN pad dialog); the rail avatar opens it again. Switch logic moves out of `SettingsViewModel` into `core/data/repo/UserSwitchRepository`.
- `GET /auth/me` drives permission-aware UI (hide Requests/Recordings actions the user lacks).
- Unit tests for the repository and the PIN pad state machine.

### Verification on the dev instance

- [ ] Acceptance scenarios 4 and 8 from the design, executed against the dev instance with a second Google account or an invite link (owner action), plus API-level checks for each permission.
