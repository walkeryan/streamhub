import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests-policy-http', fullyParallel: false, workers: 1, timeout: 60_000,
  expect: { timeout: 10_000 }, reporter: [['list']],
  use: { baseURL: 'http://127.0.0.1:5188', trace: 'retain-on-failure', serviceWorkers: 'block' },
  projects: [{ name: 'google-chrome-policy-http', use: { ...devices['Desktop Chrome'], channel: 'chrome' } }],
  webServer: {
    command: 'cd ../hub-server && ${STREAMHUB_TEST_PYTHON:-python3} -m tests.policy_http_harness --dist ../web-app/dist --port 5188',
    url: 'http://127.0.0.1:5188/api/openapi.json', reuseExistingServer: false, timeout: 30_000,
  },
})
