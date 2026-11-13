import { defineConfig, devices } from '@playwright/test'

// Tests run against the Vite dev server in mock mode (MSW fixtures), no backend needed.
// PLAYWRIGHT_BUNDLED=1 uses Playwright's own Chromium (the Playwright container image) instead of an
// installed Google Chrome. PLAYWRIGHT_MOCK_BUILD=1 serves the compiled mock build (npm run build:mock)
// instead; specs that import app modules from the dev server need the default.
const browser = process.env.PLAYWRIGHT_BUNDLED ? {} : { channel: 'chrome' }
const server = process.env.PLAYWRIGHT_MOCK_BUILD ? 'npx vite preview --outDir dist-mock' : 'VITE_MOCK=1 npx vite'
export default defineConfig({
  testDir: './tests',
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  reporter: [['list']],
  use: { baseURL: 'http://localhost:5223', trace: 'retain-on-failure', viewport: { width: 1280, height: 720 } },
  projects: [{ name: 'chrome', use: { ...devices['Desktop Chrome'], ...browser, viewport: { width: 1280, height: 720 } } }],
  webServer: {
    command: `${server} --port 5223 --strictPort`,
    url: 'http://localhost:5223',
    reuseExistingServer: false,
    timeout: 60_000,
  },
})
