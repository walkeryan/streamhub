import { expect, test } from '@playwright/test'
import { openMore, signIn } from './helpers'

test('Home rows retain titles and remote focus on desktop and mobile', async ({ page }) => {
  await signIn(page)
  await expect(page.getByTestId('hero-play')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowDown')
  // Continue Watching: wide artwork, always captioned with the title.
  const focused = page.locator('[data-row="continue"] [data-testid="tile"][data-focused="true"]')
  await expect(focused).toHaveCount(1)
  await expect(focused.locator('.cap')).not.toBeEmpty()
  await expect(focused.locator('.cap')).toHaveCSS('opacity', '1')
  const art = await focused.boundingBox()
  expect(art!.width / art!.height).toBeCloseTo(16 / 9, 1)
  await page.waitForTimeout(250) // allow focus/rail transitions to finish for visual evidence
  await page.screenshot({ path: '/tmp/streamhub-reference-home-desktop.png' })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: '/tmp/streamhub-reference-home-mobile.png' })
  await openMore(page, 'Sports')
  await expect(page).toHaveURL(/\/sports$/)
})

test('Sports cards show supplied titles without inventing scores or live-game claims', async ({ page }) => {
  await signIn(page)
  await page.evaluate(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const req = new Request(input, init)
      if (new URL(req.url).pathname !== '/api/v1/sports/events') return original(req)
      const now = Date.now()
      return Response.json({ as_of: new Date(now).toISOString(), date: new Date(now).toISOString().slice(0, 10), timezone: 'America/New_York', truncated: false, events: Array.from({ length: 6 }, (_, i) => ({ id: `fixture-${i}`, sport: 'Basketball', programme_id: `prog:${i}`, title: i === 0 ? 'Basketball highlights and analysis' : `Fixture programme ${i}`, start: new Date(now - 60000).toISOString(), stop: new Date(now + 600000).toISOString(), channel_id: `ch:fixture:${i}`, channel_name: `Fixture channel ${i}`, channel_number: i, source_name: 'Test feed' })).map((item) => ({ ...item, status: 'current', replay: false, live_evidence: false, grouping_evidence: 'same_source_programme', feeds: [{ ...item, source_id: 'fixture', language: 'eng', language_verified: false }] })), total: 6, offset: 0, limit: 40, sports: [{ name: 'Basketball', count: 6 }], coverage: { eligible_channels: 10, channels_with_sports_schedule: 6 } })
    }
  })
  await openMore(page, 'Sports')
  await expect(page.getByTestId('sports-listing')).toHaveCount(6)
  const first = page.getByTestId('sports-listing').filter({ hasText: 'Basketball highlights and analysis' })
  await first.hover()
  await expect(page.locator('.sports-card[data-focused="true"]')).toContainText('Basketball highlights and analysis')
  // On the air by the guide, which does not call it live: the card claims no more than that, and no score.
  await expect(page.getByTestId('sports-listing').first()).toContainText('On now')
  await expect(page.getByTestId('sports-listing').first()).not.toContainText('LIVE')
  await expect(first).toHaveText(/^On nowBasketballBasketball highlights and analysisFixture channel 0Started \d{1,2}:\d{2}\s?[AP]M$/)
  await page.waitForTimeout(250) // stable focus-ring screenshot
  await page.screenshot({ path: '/tmp/streamhub-reference-sports-desktop.png' })
  await page.keyboard.press('ArrowRight')
  await expect(page.locator('.sports-card[data-focused="true"]')).toHaveAttribute('aria-label', 'Watch Fixture programme 1, Basketball, on now, on Fixture channel 1')
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await first.hover()
  await page.waitForTimeout(250)
  await page.screenshot({ path: '/tmp/streamhub-reference-sports-mobile.png' })
})
