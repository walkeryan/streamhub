import { defineConfig, devices } from '@playwright/test'

// Tests run against the Vite dev server in mock mode (MSW fixtures), no backend needed.
export default defineConfig({
  testDir: './tests',
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  reporter: [['list']],
  use: { baseURL: 'http://localhost:5199', trace: 'retain-on-failure', viewport: { width: 1280, height: 720 } },
  projects: [{ name: 'chrome', use: { channel: 'chrome', ...devices['Desktop Chrome'], viewport: { width: 1280, height: 720 } } }],
  webServer: {
    command: 'VITE_MOCK=1 npx vite --port 5199 --strictPort',
    url: 'http://localhost:5199',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
})
