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
