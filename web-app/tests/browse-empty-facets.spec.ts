import { expect, test } from '@playwright/test'
import { activeFilter, closeFilters, openFilters, signIn } from './helpers'

test('active category and genre remain named when their facets come back empty', async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const request = new Request(input, init)
      const url = new URL(request.url)
      // Set by the test once both filters are chosen: from then on the catalog has nothing under them.
      const empty = sessionStorage.getItem('test.facets') === 'empty'
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
  await openFilters(page)
  await page.getByRole('button', { name: 'Category: All categories', exact: true }).click()
  await page.getByRole('button', { name: 'EN-ACTION (1,024)', exact: true }).click()
  await page.getByRole('button', { name: 'Genre: All genres', exact: true }).click()
  await page.getByRole('button', { name: 'Action (1,024)', exact: true }).click()
  await closeFilters(page)
  await expect(activeFilter(page, 'Genre: Action')).toBeVisible()
  await page.evaluate(() => sessionStorage.setItem('test.facets', 'empty'))
  await page.reload()
  await expect(page.getByRole('heading', { name: 'No matching titles', exact: true })).toBeVisible()
  await expect(activeFilter(page, 'Category: EN-ACTION')).toBeVisible()
  await expect(activeFilter(page, 'Genre: Action')).toBeVisible()
  await openFilters(page)
  await expect(page.getByRole('button', { name: 'Category: EN-ACTION', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Genre: Action', exact: true })).toBeVisible()
  await page.getByTestId('browse-filters').getByRole('button', { name: 'Clear filters', exact: true }).click()
  await expect(page.getByTestId('active-filter')).toHaveCount(0)
  await openFilters(page)
  await expect(page.getByRole('button', { name: 'Category: All categories', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Genre: All genres', exact: true })).toBeVisible()
})
