import { expect, test } from '@playwright/test'
import { closeFilters, openFilters } from '../tests/helpers'

test('TV search toggles from voice to keyboard and date jumps use the catalog release', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 })
  await page.addInitScript(() => {
    Object.assign(window, { StreamHubTV: {
      capabilities: () => JSON.stringify({ voice_search: true, inline_voice_search: true }),
      play: () => true, signedOut() {}, deliverToken() {},
      message: () => true,
    } })
  })
  await page.goto('/')
  await page.getByTestId('profile-card').filter({ hasText: 'Fixture English' }).click()
  await expect(page.getByTestId('home')).toBeVisible()
  const field = page.getByRole('textbox', { name: 'Search', exact: true })
  await field.click()
  await expect(field).toHaveAttribute('placeholder', 'Listening…')
  await field.click()
  await expect(field).toBeEditable()
  await expect(field).toBeFocused()
  await field.pressSequentially('Studio Archive')
  await expect(page).toHaveURL(/q=Studio%20Archive/)
  await page.getByRole('link', { name: 'Movies', exact: true }).click()
  await openFilters(page)
  await page.getByTestId('browse-filters').getByRole('button', { name: 'Release date', exact: true }).click()
  await closeFilters(page)
  const rail = page.getByRole('navigation', { name: 'Jump to' })
  const now = new Date()
  const month = new Intl.DateTimeFormat('en-US', { year: 'numeric', month: 'short', timeZone: 'UTC' })
  const expected = Array.from({ length: 12 }, (_, i) => month.format(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - i, 1)))
  await expect(rail.getByRole('button', { name: 'Jump to ' + expected[0], exact: true })).toBeVisible()
  const labels = await rail.getByTestId('browse-jump').allTextContents()
  expect(labels.filter(label => /^[A-Za-z]{3} \d{4}$/.test(label))).toEqual(expected)
  expect(labels).not.toContain('2050')
  await rail.getByRole('button', { name: 'Jump to 2017', exact: true }).click()
  const film = page.locator('[data-testid="tile"][data-focused="true"]')
  await expect(film).toHaveAttribute('aria-label', 'Route 2050')
  await expect(film).toContainText('2017')
})
