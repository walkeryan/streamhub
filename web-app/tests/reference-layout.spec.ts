import { expect, test } from '@playwright/test'
import { openMore, signIn } from './helpers'
import { stubSports } from './sports-helpers'

test('Home rows retain titles and remote focus on desktop and mobile', async ({ page }) => {
  await signIn(page)
  await expect(page.getByTestId('hero-play')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowDown')
  // Continue Watching: wide artwork, always captioned with the title (under the still).
  const focused = page.locator('[data-row="continue"] [data-testid="tile"][data-focused="true"]')
  await expect(focused).toHaveCount(1)
  await expect(focused.locator('.ds-still-cap')).not.toBeEmpty()
  await expect(focused.locator('.ds-still-cap')).toBeVisible()
  const art = await focused.locator('.ds-still-art').boundingBox()
  expect(art!.width / art!.height).toBeCloseTo(16 / 9, 1)
  await page.waitForTimeout(250) // allow focus transitions to finish for visual evidence
  await page.screenshot({ path: '/tmp/streamhub-reference-home-desktop.png' })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: '/tmp/streamhub-reference-home-mobile.png' })
  await openMore(page, 'Sports')
  await expect(page).toHaveURL(/\/sports$/)
})

test('Sports cards show supplied titles without inventing scores or live-game claims', async ({ page }) => {
  await stubSports(page, Array.from({ length: 6 }, (_, i) => ({
    id: String(i), title: i === 0 ? 'Basketball highlights and analysis' : `Fixture programme ${i}`,
    sport: 'Basketball', startMin: -1, lengthMin: 15, live: false,
    feeds: [{ id: String(i), name: `Fixture channel ${i}`, number: i }],
  })))
  await signIn(page)
  await page.getByTestId('nav-sports').click()
  const airing = page.getByTestId('sports-also-on')
  await expect(airing.getByTestId('sports-listing')).toHaveCount(6)
  await expect(page.getByTestId('sports-live').getByTestId('sports-listing')).toHaveCount(0)
  const first = airing.getByTestId('sports-listing').first()
  await expect(first).toHaveAttribute('data-focused', 'true')
  await expect(first).toContainText('Basketball highlights and analysis')
  await expect(first).toContainText('On now')
  await expect(first).toContainText('Fixture channel 0')
  await expect(first).toContainText(/Started \d{1,2}:\d{2}/)
  await expect(first).not.toContainText(/LIVE|\b\d+[-–]\d+\b/)
  await page.waitForTimeout(250) // stable focus-ring screenshot
  await page.screenshot({ path: '/tmp/streamhub-reference-sports-desktop.png' })
  await page.keyboard.press('ArrowRight')
  await expect(airing.locator('[data-testid=sports-listing][data-focused="true"]')).toHaveAttribute('aria-label', 'Watch Fixture programme 1, Basketball, on now, on Fixture channel 1')
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await first.hover()
  await page.waitForTimeout(250)
  await page.screenshot({ path: '/tmp/streamhub-reference-sports-mobile.png' })
})
