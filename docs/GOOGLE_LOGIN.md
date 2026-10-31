# Owner Google login and native TV pairing

Google sign-in is additive and disabled until configured. For an Internet-facing server, set
`GOOGLE_AUTH_REQUIRED=true`: password/PIN login is then rejected, including profiles without a PIN.
Existing app JWTs, refresh, and authenticated device pairing continue to work. Enabling Google-only
mode does not revoke already-issued sessions: use this deployment on a clean approved configuration,
or explicitly review/revoke legacy profiles/sessions before exposure. Anonymous profile enumeration
is blocked in Google-required mode; authenticated TV settings can still list profiles. Do not add a blanket
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
| `GOOGLE_ALLOWED_EMAILS` | Comma-separated exact owner Google emails; empty denies all |
| `GOOGLE_AUTH_REQUIRED` | `true` for the public deployment |
| `GOOGLE_OWNER_PROFILE_ID` | Optional existing admin profile ID; mandatory if multiple admins exist |

All allowed emails intentionally map to the one selected **existing owner/admin profile**; this is
not guest signup. No profile is created or promoted by Google login. Do not allow guest emails here.
A test-mode Google OAuth consent screen must include the approved owner as a test user. No Google
Drive, YouTube or other account scopes are requested: only `openid email`.

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
  authorized party, expiry, nonce and boolean `email_verified`, then applies the explicit allowlist.
- Callback creates a 60-second one-time handoff cookie and redirects to `/` or the fixed `/pair`.
  Neither Google nor StreamHub JWTs appear in URLs.
- Web makes same-origin POST `/api/v1/auth/google/complete`; Origin must match the configured HTTPS
  app origin. It receives the normal app tokens and persists them through the existing session store.
- OAuth state is single-use, including failed callbacks. Cancel/replay/expiry returns a generic
  error with a retry button. Existing terms/disclaimer acceptance remains required.

## TV usage

Install the APK and enter the HTTPS server address. Use its existing device pairing flow. On a
phone/computer, visit the displayed pairing page, sign in with the authorized Google account, and
approve the TV code. The TV receives the normal app JWT; it does not need an embedded Google login.
Pairing stays on `/pair` across Google redirects. A valid six-character TV code is retained in the
server-side OAuth transaction and restored to the fixed pairing screen; it is never sent to Google.

## Required release acceptance

Synthetic tests verify signed Google token claims, denied identities, cookie/state replay, expiry,
configuration failure, local-login blocking, app refresh and TV pairing. They do not prove the
real OAuth console configuration. Before declaring live login ready, test the configured owner in
a real browser, a denied account/cancel/retry, then actual APK pairing and playback on both devices.
