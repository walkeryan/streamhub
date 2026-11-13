# Google sign-in and native TV pairing

Anyone can sign in with Google; what they can do afterwards is decided by the server's users and
roles, not by a list of addresses. One person is one user. A first sign-in creates a user who waits
for the owner's approval (or is refused, in invite-only mode); an invite link skips the wait.

Google sign-in is additive and disabled until configured. For an Internet-facing server, set
`GOOGLE_AUTH_REQUIRED=true`: password/PIN login is then rejected, including profiles without a PIN.
Existing app JWTs, refresh, and authenticated device pairing continue to work. Enabling Google-only
mode does not revoke already-issued sessions: review the Users screen (each user's devices can be
signed out there) before exposure. Anonymous profile enumeration is blocked in Google-required
mode; a signed-in caller sees only their own profile. Do not add a blanket
Cloudflare Access gate: the native APK and media clients cannot complete an HTML login redirect.

## Authorized server configuration

Do not commit secrets. After the owner approves OAuth setup and public hosting, provision a Google
**Web application** OAuth client with exactly this authorized redirect URI:

`https://streamhub.ryan-walker.dev/api/v1/auth/google/callback`

Set environment values through the deployment's secret/config mechanism:

| Variable | Purpose |
| --- | --- |
| `GOOGLE_CLIENT_ID` | Approved Google Web OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Server-only OAuth client secret |
| `GOOGLE_REDIRECT_URI` | Exact callback above (default) |
| `GOOGLE_OWNER_EMAIL` | The one Google address that may claim the Owner user on its first sign-in |
| `GOOGLE_AUTH_REQUIRED` | `true` for the public deployment |
| `GOOGLE_OWNER_PROFILE_ID` | Optional: which existing owner profile the bootstrap address claims, when there are several |

There is no allowlist. `GOOGLE_ALLOWED_EMAILS` is retired: when `GOOGLE_OWNER_EMAIL` is unset its
first entry is still read as the bootstrap address, and nothing else about it is honoured. Remove it
once the owner has signed in.

A test-mode Google OAuth consent screen only admits its listed test users; publish the consent
screen, or add each person as a test user, before inviting anyone. No Google Drive, YouTube or other
account scopes are requested: only `openid email`.

## Who gets in

A Google account with a verified email address signs in as follows, in this order:

1. **A known account** (its Google subject is already on a user) signs in as that user. A suspended
   user is refused.
2. **The bootstrap owner.** The address in `GOOGLE_OWNER_EMAIL` claims the existing Owner user, once,
   and only while no owner has a Google identity yet. After that the variable does nothing: further
   owners are made in the Users screen by giving a user the Owner role.
3. **An invite link** (`/invite/<token>`, made in the Users screen or with `POST /api/v1/invites`)
   carries a role, an expiry and a number of uses. Signing in through it creates an active user with
   that role at once. A user already waiting for approval can use one too.
4. **Approval mode** (`signup_mode: approval`, the default): the sign-in creates a `pending` user.
   Their browser shows a waiting screen and may call `GET /api/v1/auth/me` and nothing else. Everyone
   holding `users.manage` gets a notice, and the address in the `notify_webhook` setting, if set, is
   told. Approving (with a role) or denying happens in the Users screen; once approved, the waiting
   browser is signed in without a second trip through Google.
5. **Invite-only mode** (`signup_mode: invite_only`): without an invite the sign-in is refused and
   no user is created.

`signup_mode` and `notify_webhook` are runtime settings (`PATCH /api/v1/settings`, `system.manage`).
Changing a user's role, permissions or limits, suspending or removing them applies on their next
request. A user without the `remote.access` permission can only use the server from the home
network (the `home_networks` setting), whichever way they signed in.

With the legacy `CONTENT_POLICY_ENABLED=true` regime, Google sign-in stays what it was: only an
owner signs in with Google, and the owner PIN completes the sign-in.

Use HTTPS and **one API worker**. Transactions/handoffs are bounded, expiring in-memory records;
server restart invalidates pending sign-ins safely, and the user can retry. This implementation is
not suitable for round-robin multi-worker deployment until a shared one-time store is added.
Missing OAuth config fails closed while Google-required mode stays on. Bootstrap admin password is
not printed in Google-required mode; local password login remains inaccessible.

Keep `CORS_ORIGINS` empty for same-origin production web/API, or explicitly scoped to the verified
app origin. Existing persistent `SECRET_KEY` and `/config` must be preserved across upgrades; key
rotation would log out devices. Keep the service behind the established reverse proxy/tunnel and
never expose the SQLite/config directory.

## Flow and security

- Browser starts `/api/v1/auth/google/start`; server generates one-time state, nonce and S256 PKCE.
- Secure, HTTP-only, host-only SameSite=Lax cookie binds the callback to the initiating browser.
- Server exchanges the code directly, checks Google JWKS RS256 signature, issuer, audience,
  authorized party, expiry, nonce and boolean `email_verified`, then maps the Google subject onto a
  user as described under "Who gets in". An invite token rides the flow server-side, never through Google.
- Callback creates a 60-second one-time handoff cookie and redirects to `/` or the fixed `/pair`.
  Neither Google nor StreamHub JWTs appear in URLs.
- Web makes same-origin POST `/api/v1/auth/google/complete`; Origin must match the configured HTTPS
  app origin. An active user receives the normal app tokens; a user waiting for approval receives a
  token that opens `GET /api/v1/auth/me` only.
- OAuth state is single-use, including failed callbacks. Cancel/replay/expiry returns a generic
  error with a retry button. Existing terms/disclaimer acceptance remains required.

## TV usage

Install the APK and enter the HTTPS server address. Use its existing device pairing flow. On a
phone/computer, visit the displayed pairing page, sign in with Google, and approve the TV code. The
TV is then signed in as the approving user and receives the normal app JWT; it does not need an
embedded Google login. Someone holding `users.manage` can instead approve it as a **shared household
device**: it then opens on "Who's watching?" and switches between the household's users, asking for
a user's PIN where one is set or where that user has more access than the current one.
Pairing stays on `/pair` across Google redirects. A valid six-character TV code is retained in the
server-side OAuth transaction and restored to the fixed pairing screen; it is never sent to Google.

## Required release acceptance

Synthetic tests verify signed Google token claims, the bootstrap owner, approval, invites,
invite-only refusal, suspended accounts, cookie/state replay, expiry, configuration failure,
local-login blocking, app refresh and TV pairing. They do not prove the real OAuth console
configuration. Before declaring live login ready, test the bootstrap owner in a real browser, a
second account through approval and through an invite link, a cancel/retry, then actual APK pairing
and playback on both devices.
