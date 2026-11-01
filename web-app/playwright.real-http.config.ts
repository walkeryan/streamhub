import { defineConfig, devices } from '@playwright/test'

// Compiled production-mode frontend + actual HTTP/ASGI backend. No MSW.
// Build before running: npm run build. This config never builds automatically.
export default defineConfig({
  testDir: './tests-real-http',
  fullyParallel: false,
  workers: 1,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: [['list']],
  use: { baseURL: 'http://127.0.0.1:5187', trace: 'retain-on-failure', serviceWorkers: 'block' },
  projects: [{ name: 'google-chrome-real-http', use: { ...devices['Desktop Chrome'], channel: 'chrome' } }],
  webServer: {
    command: 'cd ../hub-server && ${STREAMHUB_TEST_PYTHON:-python3} -m tests.real_http_harness --dist ../web-app/dist --port 5187',
    url: 'http://127.0.0.1:5187/api/openapi.json',
    reuseExistingServer: false,
    timeout: 30_000,
  },
})
