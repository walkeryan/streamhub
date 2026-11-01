import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('Home wide cards retain metadata and remote focus on desktop and mobile', async ({ page }) => {
  await signIn(page)
  await expect(page.getByTestId('hero-play')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowDown')
  const focused = page.locator('.feature-card[data-focused="true"]')
  await expect(focused).toHaveCount(1)
  await expect(focused.locator('.feature-copy h3')).not.toBeEmpty()
  const art = await focused.locator('.feature-art').boundingBox()
  expect(art!.width / art!.height).toBeCloseTo(16 / 9, 1)
  await page.waitForTimeout(250) // allow focus/rail transitions to finish for visual evidence
  await page.screenshot({ path: '/tmp/streamhub-reference-home-desktop.png' })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: '/tmp/streamhub-reference-home-mobile.png' })
  await page.getByRole('button', { name: 'Sports', exact: true }).click()
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
  await page.getByRole('link', { name: 'Sports', exact: true }).click()
  await expect(page.getByTestId('sports-listing')).toHaveCount(6)
  await page.getByRole('button', { name: 'View feeds for Basketball highlights and analysis', exact: true }).hover()
  await expect(page.locator('.sports-event-card[data-focused="true"]')).toContainText('Basketball highlights and analysis')
  await expect(page.getByTestId('sports-listing').first()).toContainText('On now')
  await expect(page.getByTestId('sports-listing').first()).not.toContainText('LIVE')
  await page.waitForTimeout(250) // stable focus-ring screenshot
  await page.screenshot({ path: '/tmp/streamhub-reference-sports-desktop.png' })
  await page.keyboard.press('ArrowRight')
  await expect(page.locator('.sports-event-card[data-focused="true"]')).toHaveAttribute('aria-label', 'View feeds for Fixture programme 1')
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.getByRole('button', { name: 'View feeds for Basketball highlights and analysis', exact: true }).hover()
  await page.waitForTimeout(250)
  await page.screenshot({ path: '/tmp/streamhub-reference-sports-mobile.png' })
})
