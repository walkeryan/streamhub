# Local Chrome / real HTTP contract tests

This separate suite serves the **compiled, non-MSW frontend** from the actual FastAPI app. It uses normal fixture profile login, actual endpoint schemas, SQLite queries, audio selection, Sports EPG and cache paths. It does not validate production Google OAuth or provider media decoding.

The server binds only `127.0.0.1:5187`. Its temporary database, profiles and credentials are synthetic and deleted on normal exit. Every backend upstream HTTP request uses a local `MockTransport`; unknown requests fail. Browser requests to other origins are blocked. FFmpeg is disabled. No production configuration is loaded or changed.

Coordinate with other Chrome/build users before running:

```sh
cd web-app
npm run build
STREAMHUB_TEST_PYTHON=/path/to/test-venv/bin/python npx playwright test --config playwright.real-http.config.ts
```

Use the installed **Google Chrome** channel. The suite creates an isolated browser context, never the owner's authenticated browser profile. It uses one worker and refuses to reuse an existing server. Do not set `VITE_MOCK=1` for this build.

Lightweight fixture validation, without starting Chrome or a TCP listener:

```sh
cd hub-server
python -m pytest -q tests/test_real_http_harness.py
```

Coverage: canonical relevance, unavailable metadata titles, raw provider-label suppression, Back/query persistence, repeat real search responses, authenticated options, explicit exact-variant consent, Sports programme data and profile-isolated audio/history. Media delivery intentionally has no playable bytes; successful resolve is not reported as successful playback.

## Policy-enabled owner-PIN contract fixture

After building the same compiled web app, run `npx playwright test --config playwright.policy-http.config.ts` with `STREAMHUB_TEST_PYTHON` set as above. This separate single-worker Google Chrome suite binds only `127.0.0.1:5188` and uses temporary SQLite. Lightweight validation: `python -m pytest -q tests/test_policy_http_harness.py tests/test_owner_pin_http_recovery.py tests/test_owner_pin_hash_cost.py`.

The fixture uses synthetic Google identity claims, without an `auth_time` claim, and a fixture-only Google handoff redirect. The browser calls the actual `/auth/google/complete` endpoint and receives a pending identity, never an owner session until PIN verification. A fixture-only route invokes the local enrollment-approval service for the exact displayed setup request. No production VM command or real PIN is used. The synthetic PIN and private fixture database are disposable. These helper routes are never registered by the production app.

The Google service origin is overridden to the local HTTP fixture; production HTTPS cookie flags remain unchanged. All upstream traffic terminates in local mock transport; Chrome external traffic is blocked. This suite does not prove production TLS, Google signature verification, real provider decoding, or actual TV behavior.

Coverage: pending-only Google identity, local approval prerequisite, private PIN setup, action/target-bound PIN approval for policy editing, owner-to-restricted switching, unknown catalog/direct-play denial, wrong PIN, cancellation and correct-PIN return, clearing browser storage with retained Google identity still requiring PIN, no SSO-authorized recovery, persistent failed-attempt limits and UI cooldown. Separate actual-HTTP fixtures exercise local-approved recovery, old PIN/access/refresh/action/media invalidation, and simulated nine-minute setup-grant / eleven-minute pending-login expiry without waiting. Production-cost scrypt hashing is exercised without reducing cost parameters. This replaces the earlier synthetic fresh-Google reauthentication acceptance prototype; no unsupported Google freshness claim is made.

Verified locally against combined application revision `c1e7916`: production web build, one compiled Google Chrome owner-PIN flow, and the three existing real-HTTP Chrome regressions passed. The fixture helper GET routes are registered before the SPA fallback; no production route or cookie-security override is required.
