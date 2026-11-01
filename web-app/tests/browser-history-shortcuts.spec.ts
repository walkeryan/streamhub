import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('Alt arrows leave focus alone; browser history restores filters and plain Left opens the rail', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await signIn(page)
  await expect(page.getByTestId('hero-play')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowLeft')
  await expect(page.getByTestId('rail')).toHaveAttribute('data-expanded', 'true')
  await page.getByRole('link', { name: 'Movies', exact: true }).click()
  await expect(page.getByTestId('discover')).toBeVisible()
  await page.getByRole('button', { name: 'My library', exact: true }).click()
  await page.getByRole('button', { name: 'Year', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Year', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('link', { name: 'Series', exact: true }).click()
  await expect(page).toHaveURL(/\/discover\/shows$/)
  const search = page.getByLabel('Release year')
  await search.click()
  await expect(search).toBeFocused()
  await page.keyboard.press('Alt+ArrowLeft')
  await expect(search).toBeFocused()
  await expect(page.getByTestId('rail')).toHaveAttribute('data-expanded', 'false')
  // Headless Chrome does not execute browser-chrome accelerators. Exercise its
  // history separately; the companion test proves our handler leaves defaults intact.
  await page.goBack()
  await expect(page).toHaveURL(/\/browse\/movies$/)
  await expect(page.getByRole('button', { name: 'Year', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.keyboard.press('Alt+ArrowRight')
  await page.goForward()
  await expect(page).toHaveURL(/\/discover\/shows$/)
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  expect(errors).toEqual([])
  await page.screenshot({ path: '/tmp/streamhub-browser-history-shortcuts.png' })
})

test('modified arrow events remain uncancelled even in a search field', async ({ page }) => {
  await signIn(page)
  await page.getByRole('link', { name: 'Search', exact: true }).click()
  const input = page.getByLabel('Search titles and channels')
  await expect(input).toBeFocused()
  const outcomes = await input.evaluate((element) => ['ArrowLeft', 'ArrowRight'].map((key) => {
    const event = new KeyboardEvent('keydown', { key, code: key, altKey: true, bubbles: true, cancelable: true })
    return { allowed: element.dispatchEvent(event), prevented: event.defaultPrevented }
  }))
  expect(outcomes).toEqual([{ allowed: true, prevented: false }, { allowed: true, prevented: false }])
  await expect(input).toBeFocused()
  await expect(page.getByTestId('rail')).toHaveAttribute('data-expanded', 'false')
})
