import { expect, test } from '@playwright/test'
import { activeFilter, closeFilters, openFilters, searchBox, signIn } from './helpers'

test('Alt arrows leave focus alone; browser history restores filters and plain Up reaches the bar', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await signIn(page)
  await expect(page.getByTestId('hero-play')).toHaveAttribute('data-focused', 'true')
  const inBar = page.getByTestId('rail').locator('[data-focused="true"]')
  await page.keyboard.press('ArrowUp')
  await expect(inBar).toHaveCount(1)
  await page.getByRole('link', { name: 'Movies', exact: true }).click()
  await openFilters(page)
  await page.getByRole('button', { name: 'Year', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Year', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await closeFilters(page)
  await page.getByRole('link', { name: 'Shows', exact: true }).click()
  await expect(page).toHaveURL(/\/browse\/shows$/)
  // The screen takes focus from the bar once its shelves are in.
  await expect(inBar).toHaveCount(0)
  // A text field keeps its caret through Alt+Left, and the bar does not walk to its neighbour.
  const search = searchBox(page)
  await search.click()
  await expect(search).toBeFocused()
  await page.keyboard.press('Alt+ArrowLeft')
  await expect(search).toBeFocused()
  await expect(page.getByTestId('nav-search')).toHaveAttribute('data-focused', 'true')
  await expect(inBar).toHaveCount(1)
  // Headless Chrome does not execute browser-chrome accelerators. Exercise its
  // history separately; the companion test proves our handler leaves defaults intact.
  await page.goBack()
  await expect(page).toHaveURL(/\/browse\/movies$/)
  await expect(activeFilter(page, 'Sort: Year')).toBeVisible()
  await page.keyboard.press('Alt+ArrowRight')
  await page.goForward()
  await expect(page).toHaveURL(/\/browse\/shows$/)
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  expect(errors).toEqual([])
  await page.screenshot({ path: '/tmp/streamhub-browser-history-shortcuts.png' })
})

test('modified arrow events remain uncancelled even in a search field', async ({ page }) => {
  await signIn(page)
  const input = searchBox(page)
  await input.click()
  await expect(input).toBeFocused()
  const outcomes = await input.evaluate((element) => ['ArrowLeft', 'ArrowRight'].map((key) => {
    const event = new KeyboardEvent('keydown', { key, code: key, altKey: true, bubbles: true, cancelable: true })
    return { allowed: element.dispatchEvent(event), prevented: event.defaultPrevented }
  }))
  expect(outcomes).toEqual([{ allowed: true, prevented: false }, { allowed: true, prevented: false }])
  await expect(input).toBeFocused()
  await expect(page.getByTestId('rail').locator('[data-focused="true"]')).toHaveAttribute('data-testid', 'nav-search')
})
