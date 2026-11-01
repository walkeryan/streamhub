import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('active category and genre remain named when a search empties their facets', async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const request = new Request(input, init)
      const url = new URL(request.url)
      const empty = url.searchParams.get('q') === 'zzq-no-such-title-qa'
      if (url.pathname === '/api/v1/browse/movies/categories') {
        return Response.json(empty ? [] : [{ name: 'EN-ACTION', count: 1024 }])
      }
      if (url.pathname === '/api/v1/browse/movies/genres') {
        return Response.json(empty ? [] : [{ name: 'action', count: 1024 }])
      }
      if (empty && url.pathname === '/api/v1/browse/movies') {
        return Response.json({ tiles: [], total: 0, page: 1, page_size: 60, has_more: false })
      }
      return original(request)
    }
  })
  await signIn(page)
  await page.getByRole('link', { name: 'Movies', exact: true }).click()
  await expect(page.getByTestId('discover')).toBeVisible()
  await page.getByRole('button', { name: 'My library', exact: true }).click()
  await page.getByRole('button', { name: 'Category: All categories', exact: true }).click()
  await page.getByRole('button', { name: 'EN-ACTION (1,024)', exact: true }).click()
  await page.getByRole('button', { name: 'Genre: All genres', exact: true }).click()
  await page.getByRole('button', { name: 'Action (1,024)', exact: true }).click()
  await page.getByLabel('Search this library').fill('zzq-no-such-title-qa')
  await expect(page.getByRole('heading', { name: 'No matching titles', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Category: EN-ACTION', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Genre: Action', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Clear filters', exact: true }).first().click()
  await expect(page.getByRole('button', { name: 'Category: All categories', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Genre: All genres', exact: true })).toBeVisible()
})
