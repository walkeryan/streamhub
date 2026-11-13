import { expect, test } from '@playwright/test'
import { search, signIn } from './helpers'

for (const width of [375, 540, 1280]) {
  test(`Search results fit at ${width}px: every row and poster inside the page, nothing above them but the phone's box`, async ({ page }) => {
    await signIn(page)
    await page.setViewportSize({ width, height: 844 })
    await search(page, 'harbor')
    const results = page.getByTestId('search-results')
    await expect(results.getByTestId('tile').first()).toBeVisible()
    const content = (await results.boundingBox())!
    expect(content.width).toBeGreaterThan(width * 0.85)
    // A phone has the box at the top of the page; a wide screen has it in the bar and the results start the page.
    const first = (await results.locator('section').first().boundingBox())!
    const above = width <= 720 ? (await page.getByRole('search').boundingBox())! : (await page.getByTestId('rail').boundingBox())!
    expect(first.y).toBeGreaterThanOrEqual(above.y + above.height)
    expect(first.y - (above.y + above.height)).toBeLessThan(40)
    for (const item of await results.locator('[data-testid="tile"], [data-testid="search-broadcast"], [data-testid="search-channel"]').all()) {
      const rect = (await item.boundingBox())!
      expect(rect.x).toBeGreaterThanOrEqual(content.x)
      expect(rect.x + rect.width).toBeLessThanOrEqual(content.x + content.width + 1)
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
}
