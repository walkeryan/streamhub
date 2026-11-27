import { expect, test } from '@playwright/test'

for (const viewport of [{ width: 1280, height: 720 }, { width: 1920, height: 1080 }]) {
  test(`Home renders a saved watchlist during a catalog refresh at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await page.goto('/')
    const home = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/v1/home')
    await page.getByTestId('profile-card').filter({ hasText: 'Fixture Refresh' }).click()
    expect((await home).status()).toBe(200)
    await expect(page.getByTestId('home')).toBeVisible()
    await expect(page.locator('[data-row="watchlist"]').getByRole('link').first()).toBeVisible()
    await page.reload()
    await expect(page.locator('[data-row="watchlist"]')).toBeVisible()
  })
}
