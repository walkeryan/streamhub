import { expect, test } from '@playwright/test'
import { activeFilter, closeFilters, openFilters, signIn } from './helpers'

test('movies sort either way and jump from the side rail', async ({ page }) => {
  await signIn(page)
  await page.getByRole('link', { name: 'Movies', exact: true }).click()
  await openFilters(page)
  const sheet = page.getByTestId('browse-filters')
  await sheet.getByRole('button', { name: 'Title', exact: true }).click()
  await expect(sheet.getByTestId('browse-order-asc')).toHaveAttribute('aria-pressed', 'true')
  await closeFilters(page)
  const tiles = page.getByTestId('browse').getByTestId('tile')
  const first = await tiles.first().getAttribute('aria-label')
  // The bar reverses the order in one press, and says which way it now runs.
  await page.getByTestId('browse-order').click()
  await expect(page.getByTestId('browse-order')).toHaveText('Z to A')
  await expect(activeFilter(page, 'Sort: Title, z to a')).toBeVisible()
  await expect(tiles.first()).not.toHaveAttribute('aria-label', first ?? '')
  await page.getByTestId('browse-order').click()
  await expect(tiles.first()).toHaveAttribute('aria-label', first ?? '')
  // A to Z: the rail lists the letters titles start with; a jump lands on the first of them.
  const jumps = page.getByTestId('browse-jump')
  expect(await jumps.count()).toBeGreaterThan(1)
  const last = jumps.last()
  const letter = (await last.textContent()) ?? ''
  await last.click()
  await expect(page.locator('[data-testid="tile"][data-focused="true"]')).toHaveAttribute('aria-label', new RegExp(`^${letter}`, 'i'))
  await expect(last).toHaveAttribute('aria-current', 'true')
})

for (const section of ['Movies', 'Shows']) {
  test(`${section} explicitly selecting Recently added opens the ordered grid`, async ({ page }) => {
    await signIn(page)
    await page.getByRole('link', { name: section, exact: true }).click()
    await openFilters(page)
    await page.getByTestId('browse-filters').getByRole('button', { name: 'Recently added', exact: true }).click()
    await closeFilters(page)
    await expect(page.getByTestId('browse')).toHaveAttribute('data-view', 'grid')
    await expect(page.getByTestId('browse-order')).toHaveText('Newest first')
    await page.getByTestId('browse-order').click()
    await expect(page.getByTestId('browse-order')).toHaveText('Oldest first')
  })
}

for (const width of [1280, 1920]) {
  test(`the date rail keeps the full selected marker visible at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width * 9 / 16 })
    await signIn(page)
    await page.getByRole('link', { name: 'Movies', exact: true }).click()
    await openFilters(page)
    await page.getByTestId('browse-filters').getByRole('button', { name: 'Release date', exact: true }).click()
    await closeFilters(page)
    const stops = page.getByTestId('browse-jump')
    await expect(stops.first()).toBeVisible()
    const count = await stops.count()
    expect(count).toBeGreaterThan(1)
    for (const index of new Set([0, Math.floor(count / 2), count - 1])) {
      const stop = stops.nth(index)
      await stop.click()
      await expect(stop).toHaveAttribute('aria-current', 'true')
      await stop.scrollIntoViewIfNeeded()
      expect(await stop.evaluate((el) => {
        const r = el.getBoundingClientRect()
        return [r.left + 2, r.right - 2].every(x => el.contains(document.elementFromPoint(x, r.top + r.height / 2)))
      })).toBe(true)
    }
  })
}
