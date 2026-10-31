import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

const bg = (page: import('@playwright/test').Page) => page.evaluate(() => getComputedStyle(document.body).backgroundColor)

test('theme switch updates data-theme live and persists', async ({ page }) => {
  await signIn(page)
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'midnight')
  const midnight = await bg(page)

  await page.goto('/settings?tab=appearance')
  await expect(page.getByTestId('theme-light')).toBeVisible()
  await page.getByTestId('theme-light').click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  expect(await bg(page)).not.toBe(midnight)
  expect(await bg(page)).toBe('rgb(246, 247, 249)')

  // survives a reload (profile theme + local cache)
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
})

test('theme picker is operable with the keyboard alone', async ({ page }) => {
  await signIn(page)
  await page.goto('/settings?tab=appearance')
  await expect(page.getByTestId('theme-light')).toBeVisible()
  await expect(page.getByTestId('tab-appearance')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowDown') // first theme card (Midnight)
  await page.keyboard.press('ArrowRight') // OLED Black
  await page.keyboard.press('Enter')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'oled')
  await page.keyboard.press('ArrowRight') // Daylight
  await page.keyboard.press('ArrowDown') // second row: Poster Adaptive is nearest
  await page.keyboard.press('ArrowLeft') // High Contrast
  await page.keyboard.press('Enter')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'contrast')
  expect(await bg(page)).toBe('rgb(0, 0, 0)')
})
