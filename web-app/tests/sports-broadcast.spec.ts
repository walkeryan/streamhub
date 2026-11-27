import { expect, test } from '@playwright/test'
import { signIn } from './helpers'
import { stubSports, type StubEvent } from './sports-helpers'

test('only confirmed live games appear in Live now while on-air replays and unknowns remain available', async ({ page }) => {
  const fixture: StubEvent[] = [
    { id: 'live', title: 'City Cup', live: true },
    { id: 'unknown', title: 'Cup studio', live: false },
    { id: 'repeat', title: 'Cup encore', replay: true, live: false },
  ].map((e, i) => ({ ...e, sport: 'Soccer', startMin: -10, lengthMin: 90, feeds: [{ id: String(i), number: i + 1 }] }))
  await stubSports(page, fixture)
  await signIn(page)
  await page.goto('/sports')
  const live = page.getByTestId('sports-live')
  await expect(live.getByTestId('sports-listing')).toHaveCount(1)
  await expect(live).toContainText('City Cup')
  const other = page.getByTestId('sports-also-on')
  await expect(other.getByTestId('sports-listing')).toHaveCount(2)
  await expect(other.getByRole('button', { name: /Cup encore/ })).toContainText('Replay')
  await expect(other.getByRole('button', { name: /Cup studio/ })).toContainText('On now')
})

test('an upcoming-only day initially focuses the first visible game and returns there from details', async ({ page }) => {
  await stubSports(page, [
    { id: 'later', title: 'Evening City Cup', sport: 'Soccer', startMin: 90, feeds: [{ id: '1' }] },
  ])
  await signIn(page)
  await page.goto('/sports')
  const first = page.getByTestId('sports-row').getByRole('button', { name: /Evening City Cup/ })
  await expect(first).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('sports-sheet')).toContainText('Evening City Cup')
  await page.keyboard.press('Escape')
  await expect(first).toHaveAttribute('data-focused', 'true')
})
