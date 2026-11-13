import { expect, test } from '@playwright/test'
import { openMore, signIn } from './helpers'
import { fits, listing, stubSports, type StubEvent } from './sports-helpers'

const MANY: StubEvent[] = Array.from({ length: 501 }, (_, i) => ({
  id: `fixture-${i}`, title: `Fixture game ${i}`, sport: i % 2 ? 'Tennis' : 'Basketball', startMin: -1 - i / 1000, lengthMin: 600, feeds: [{ id: String(i), name: `Fixture channel ${i}`, number: i }],
}))

test('a large day is paged, narrowed by sport and search, and one press plays the matching channel', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await stubSports(page, [...MANY].reverse())
  await signIn(page)
  await openMore(page, 'Sports')
  await expect(page).toHaveURL(/\/sports$/)
  await expect(page.getByTestId('sports-listing')).toHaveCount(100)
  await expect(page.getByTestId('sports-chip')).toHaveText(['All · 501', 'Basketball · 251', 'Tennis · 250'])
  await expect(page.getByText('Page 1 of 6', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Next', exact: true }).click()
  await expect(page.getByText('Page 2 of 6', { exact: true })).toBeVisible()
  await expect(page.getByTestId('sports-listing')).toHaveCount(100)
  await expect(listing(page, 'Fixture game 400')).toHaveCount(1)
  await page.getByRole('button', { name: 'Tennis · 250', exact: true }).click()
  await expect(page.getByText('Page 1 of 3', { exact: true })).toBeVisible()
  await expect(listing(page, 'Fixture game 499')).toHaveCount(1)
  await expect(listing(page, 'Fixture game 500')).toHaveCount(0)
  await page.getByTestId('sports-filters').click()
  await page.getByLabel('Search games, teams or channels').fill('game 499')
  await expect(page.getByTestId('sports-listing')).toHaveCount(1)
  await page.getByRole('button', { name: 'Done', exact: true }).click()
  await expect(page.getByTestId('sports-filter-summary')).toContainText('“game 499”')
  await expect(page.getByTestId('sports-listing')).toContainText('Fixture channel 499 · Ch 499')
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await fits(page)).toBe(true)
  expect(errors).toEqual([])
  // One channel carries it: no dialog, straight to the player.
  await listing(page, 'Fixture game 499').click()
  await expect(page).toHaveURL(/\/play\/ch%3Afixture%3A499$/)
  await expect(page.getByRole('dialog')).toHaveCount(0)
})

test('an empty day says what is true and offers no dead end', async ({ page }) => {
  await stubSports(page, [])
  await signIn(page)
  await openMore(page, 'Sports')
  await expect(page.getByTestId('sports-live')).toContainText('No games are live right now.')
  await expect(page.getByTestId('sports-live')).not.toContainText('Next:')
  await expect(page.getByTestId('sports-upcoming')).toContainText('Nothing more is listed for today or tomorrow.')
  await expect(page.getByTestId('sports-chip')).toHaveText(['All · 0'])
  await expect(page.getByRole('button', { name: 'Next', exact: true })).toHaveCount(0)
  // No coverage statistics and no caveats: an empty list is just empty.
  await expect(page.getByTestId('sports')).not.toContainText(/eligible|unverified|does not mean|guide window/i)
  await expect(page.getByTestId('sports-channels')).toHaveCount(0)
  await page.getByTestId('sports-filters').click()
  await page.getByRole('button', { name: 'All languages', exact: true }).click()
  await page.getByRole('button', { name: 'Done', exact: true }).click()
  await expect(page.getByTestId('sports-live')).toContainText('No games are live right now.')
  await expect(page.locator('.fx[data-focused="true"]')).toHaveCount(1)
})
