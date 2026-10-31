import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('sources: streaming service toggle and Test link opens the service web_url', async ({ page }) => {
  await signIn(page)
  await page.goto('/sources')
  const hulu = page.getByTestId('svc-hulu')
  await expect(hulu).toHaveAttribute('aria-pressed', 'false')
  await hulu.click()
  await expect(hulu).toHaveAttribute('aria-pressed', 'true')
  const [popup] = await Promise.all([
    page.waitForEvent('popup'),
    page.getByLabel('Test link for Hulu').click(),
  ])
  expect(popup.url()).toContain('hulu.com')
})

test('sources: M3U form validates before sending', async ({ page }) => {
  await signIn(page)
  await page.goto('/sources')
  await page.getByTestId('add-m3u').click()
  await page.getByRole('button', { name: 'Add source' }).click()
  await expect(page.getByText('Give the source a name')).toBeVisible()
  await expect(page.getByText('Add a playlist address or choose a file')).toBeVisible()
})

test('pairing: approve a TV code', async ({ page }) => {
  await signIn(page)
  await page.goto('/pair')
  await page.getByLabel('TV code').fill('abc12')
  await expect(page.getByTestId('pair-approve')).toHaveAttribute('aria-disabled', 'true')
  await page.getByLabel('TV code').fill('abc123')
  await page.getByTestId('pair-approve').click()
  await expect(page.getByTestId('pair-done')).toBeVisible()
})

test('title detail: availability attribution and hand-off labelling', async ({ page }) => {
  await signIn(page)
  await page.goto('/title/' + encodeURIComponent('tmdb:movie:105')) // Netflix only
  await expect(page.getByTestId('title-play')).toContainText('Open in app')
  await expect(page.getByTestId('available-on')).toContainText('Availability data by JustWatch')
})
