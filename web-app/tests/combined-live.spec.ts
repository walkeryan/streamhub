import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('one Live TV destination shares filters and selection without a hidden preview player', async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const request = new Request(input, init)
      const response = await original(request)
      if (request.method === 'GET' && new URL(request.url).pathname === '/api/v1/sources' && response.ok) {
        return Response.json([...(await response.json()), { id: 'second-fixture', type: 'm3u', name: 'Second fixture source', enabled: true, priority: 99, status: 'ok', counts: {} }])
      }
      return response
    }
  })
  await signIn(page)
  await page.getByRole('link', { name: 'Live TV', exact: true }).click()
  await expect(page.getByRole('link', { name: 'Guide', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'English', exact: true }).click()
  await page.getByRole('button', { name: 'Source: All sources', exact: true }).click()
  await page.getByRole('dialog', { name: 'Source', exact: true }).getByRole('button', { name: 'IPTV Provider', exact: true }).click()
  await page.getByRole('button', { name: 'Category: All categories', exact: true }).click()
  await page.getByRole('dialog', { name: 'Category', exact: true }).getByRole('button', { name: /^Sports \(/ }).click()
  await page.getByRole('button', { name: 'Favorites only', exact: true }).click()
  await page.getByLabel('Search channels or number').fill('Metro')
  await expect(page.getByTestId('channel-row')).toHaveCount(1)
  await page.getByTestId('channel-row').hover()
  await expect(page.getByTestId('live-info')).toContainText('Metro Sports 1')
  await page.getByRole('button', { name: 'Preview off', exact: true }).click()
  await expect(page.getByTestId('live-preview')).toHaveAttribute('data-state', 'playing')
  await page.getByRole('button', { name: 'Guide', exact: true }).click()
  await expect(page.getByTestId('guide')).toBeVisible()
  await expect(page.getByTestId('live-preview')).toHaveCount(0)
  await expect(page.locator('video')).toHaveCount(0)
  await expect(page.getByLabel('Search channels or number')).toHaveValue('Metro')
  await expect(page.getByRole('button', { name: 'English', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('button', { name: 'Category: Sports', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Source: IPTV Provider', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Favorites only', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('guide').getByRole('button', { name: '2 Metro Sports 1', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Channels', exact: true }).click()
  await expect(page.getByTestId('guide')).toHaveCount(0)
  await expect(page.getByTestId('channel-row')).toHaveCount(1)
  await expect(page.getByTestId('live-info')).toContainText('Metro Sports 1')
})

test('legacy Guide links open the combined Live TV guide and Back restores its view', async ({ page }) => {
  await signIn(page)
  await page.goto('/guide')
  await expect(page).toHaveURL(/\/live\?view=guide$/)
  await expect(page.getByRole('heading', { name: 'Live TV', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Guide', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('video')).toHaveCount(0)
  await page.getByTestId('guide').getByRole('button', { name: '1 Harbor News 24', exact: true }).click()
  await expect(page.getByTestId('player')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('guide')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Guide', exact: true })).toHaveAttribute('aria-pressed', 'true')
})
