import { expect, test } from '@playwright/test'
import { search, signIn } from './helpers'

test('rotation keeps an unsubmitted search draft focused until its debounce submits', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await signIn(page)
  await page.getByRole('link', { name: 'Search', exact: true }).click()
  const phoneInput = page.getByTestId('search-page-input').getByRole('textbox')
  await phoneInput.focus()
  await page.keyboard.type('Star Wars')
  await page.setViewportSize({ width: 844, height: 390 })

  const wideInput = page.getByTestId('nav-search').getByRole('textbox')
  await expect(wideInput).toHaveValue('Star Wars')
  await expect(wideInput).toBeFocused()
  await expect.poll(() => wideInput.evaluate(el => ({
    start: (el as HTMLInputElement).selectionStart,
    end: (el as HTMLInputElement).selectionEnd,
  }))).toEqual({ start: 9, end: 9 })
  await expect(page).toHaveURL(/\/search\?q=Star(?:%20|\+)Wars$/)
})

test('rotation keeps focus and the exact selection in a submitted search term', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await signIn(page)
  await page.getByRole('link', { name: 'Search', exact: true }).click()
  const phoneInput = page.getByTestId('search-page-input').getByRole('textbox')
  await phoneInput.fill('Star Wars')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/search\?q=Star(?:%20|\+)Wars$/)
  await phoneInput.evaluate((el) => (el as HTMLInputElement).setSelectionRange(2, 4, 'forward'))
  await expect.poll(() => phoneInput.evaluate(el => ({
    start: (el as HTMLInputElement).selectionStart,
    end: (el as HTMLInputElement).selectionEnd,
    direction: (el as HTMLInputElement).selectionDirection,
  }))).toEqual({ start: 2, end: 4, direction: 'forward' })
  await page.setViewportSize({ width: 844, height: 390 })

  const wideInput = page.getByTestId('nav-search').getByRole('textbox')
  await expect(wideInput).toBeFocused()
  await expect.poll(() => wideInput.evaluate(el => ({
    start: (el as HTMLInputElement).selectionStart,
    end: (el as HTMLInputElement).selectionEnd,
    direction: (el as HTMLInputElement).selectionDirection,
  }))).toEqual({ start: 2, end: 4, direction: 'forward' })
  await page.setViewportSize({ width: 390, height: 844 })
  const returnedInput = page.getByTestId('search-page-input').getByRole('textbox')
  await expect(returnedInput).toBeFocused()
  await expect.poll(() => returnedInput.evaluate(el => ({
    start: (el as HTMLInputElement).selectionStart,
    end: (el as HTMLInputElement).selectionEnd,
    direction: (el as HTMLInputElement).selectionDirection,
  }))).toEqual({ start: 2, end: 4, direction: 'forward' })
})

for (const width of [375, 540, 1280]) {
  test(`Search results fit at ${width}px: every row and poster inside the page, nothing above them but the phone's box`, async ({ page }) => {
    await signIn(page)
    await page.setViewportSize({ width, height: 844 })
    if (width <= 720) await expect(page.getByRole('link', { name: 'Search', exact: true })).toBeVisible()
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
