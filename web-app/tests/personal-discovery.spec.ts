import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

async function open(page: Page, route: string) {
  await signIn(page)
  await page.goto(route)
}

test('recommendation votes toggle, removing one can be undone, and group viewing hides personal feedback', async ({ page }) => {
  await open(page, '/for-you')
  await expect(page.getByRole('heading', { name: 'Movies for you' })).toBeVisible()
  const first = page.getByRole('button', { name: /^Thumbs up:/ }).first()
  const title = (await first.getAttribute('aria-label'))!.slice('Thumbs up: '.length)
  await first.click()
  await expect(first).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: 'Thumbs down: ' + title, exact: true }).click()
  const undo = page.getByRole('button', { name: 'Undo', exact: true })
  await expect(undo).toBeFocused()
  await undo.click()
  await expect(page.getByRole('button', { name: 'Thumbs up: ' + title, exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: 'Find something together' }).click()
  await page.getByRole('button', { name: 'Create invitation' }).click()
  await expect(page.getByRole('heading', { name: 'Together', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /^Thumbs/ })).toHaveCount(0)
  await page.getByRole('button', { name: 'Leave group' }).click()
  await expect(page.getByRole('heading', { name: 'For You', exact: true })).toBeVisible()
})

test('removing one recent query and undoing it preserves the other saved query', async ({ page }) => {
  await open(page, '/search?q=harbor')
  await page.getByTestId('search-titles').getByTestId('tile').first().click()
  await page.goto('/search?q=metro')
  await page.getByTestId('search-channel').first().click()
  await page.goto('/search')
  const remove = page.getByRole('button', { name: 'Remove harbor from recent searches' })
  await remove.click()
  await expect(remove).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Remove metro from recent searches' })).toBeVisible()
  await page.getByRole('button', { name: 'Undo', exact: true }).click()
  await expect(page.getByRole('button', { name: 'harbor', exact: true })).toBeFocused()
})

test('large profile reading size persists independently of poster density and phone content reflows', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await open(page, '/settings?tab=appearance')
  await page.getByRole('radiogroup', { name: 'Text size' }).getByRole('radio', { name: 'largest' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-reading-size', 'largest')
  for (const route of ['/settings?tab=appearance', '/favorites', '/search', '/for-you']) {
    await page.goto(route)
    await expect(page.locator('html')).toHaveAttribute('data-reading-size', 'largest')
    await expect(page.locator('html')).toHaveAttribute('data-poster-size', 'standard')
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  }
})

test('the actor page displays verified roles and years and opens a filmography title', async ({ page }) => {
  await open(page, '/search/actor/72?name=Casey%20Vale')
  await expect(page.getByRole('heading', { name: 'Known for' })).toBeVisible()
  await page.getByRole('button', { name: 'Movies', exact: true }).click()
  const film = page.locator('[data-focus-key^="credits:all:tmdb:movie:"]').first()
  await expect(film).toContainText(/\d{4} · Movie · Fixture lead/)
  await film.click()
  await expect(page.getByTestId('title-detail')).toBeVisible()
})
