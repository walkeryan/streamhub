import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

for (const width of [375, 540, 1280]) {
  test(`Search results fit at ${width}px without squeezing the poster column`, async ({ page }) => {
    await signIn(page)
    await page.setViewportSize({ width, height: 844 })
    await page.getByRole('link', { name: 'Search', exact: true }).click()
    await page.getByLabel('Search titles and channels').fill('harbor')
    const results = page.getByTestId('search-results')
    await expect(results.getByTestId('tile').first()).toBeVisible()
    const controls = await page.getByTestId('search-controls').boundingBox()
    const content = await results.boundingBox()
    expect(controls).not.toBeNull()
    expect(content).not.toBeNull()
    if (width < 1024) {
      expect(content!.y).toBeGreaterThanOrEqual(controls!.y + controls!.height)
      expect(content!.width).toBeGreaterThan(width * 0.85)
      expect(Math.abs(content!.x - controls!.x)).toBeLessThan(1)
    } else {
      const rem = await page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).fontSize))
      expect(Math.abs(controls!.width - 22 * rem)).toBeLessThan(1)
      expect(content!.x).toBeGreaterThanOrEqual(controls!.x + controls!.width)
      expect(Math.abs(content!.y - controls!.y)).toBeLessThan(1)
    }
    for (const tile of await results.getByTestId('tile').all()) {
      const rect = await tile.boundingBox()
      expect(rect!.x).toBeGreaterThanOrEqual(content!.x)
      expect(rect!.x + rect!.width).toBeLessThanOrEqual(content!.x + content!.width + 1)
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `/tmp/streamhub-search-${width}.png`, fullPage: true })
  })
}
