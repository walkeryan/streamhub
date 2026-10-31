import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('source changes refresh genre facets with bounded polling and stable snapshots do not refetch catalog', async ({ page }) => {
  await page.addInitScript(() => {
    const state = window as unknown as { sourceSnapshot: { status: string; last_sync: string; count: number }; sourceReads: number; genreReads: number }
    state.sourceSnapshot = { status: 'ok', last_sync: '2026-10-31T18:00:00Z', count: 1 }
    state.sourceReads = 0; state.genreReads = 0
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const req = new Request(input, init), path = new URL(req.url).pathname
      if (path === '/api/v1/sources') {
        state.sourceReads++
        return Response.json([{ id: 's_xt', type: 'xtream', name: 'Fixture source', enabled: true, priority: 1, status: state.sourceSnapshot.status, last_sync: state.sourceSnapshot.last_sync, counts: { movies: state.sourceSnapshot.count } }])
      }
      if (path === '/api/v1/browse/movies/genres') {
        state.genreReads++
        return Response.json([{ name: state.sourceSnapshot.count > 1 ? 'comedy' : 'drama', count: state.sourceSnapshot.count }])
      }
      return original(req)
    }
  })
  await page.clock.install()
  await signIn(page)
  await page.getByRole('link', { name: 'Movies', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Genre: All genres', exact: true })).toBeVisible()
  const baseline = await page.evaluate(() => (window as unknown as { genreReads: number }).genreReads)
  await page.evaluate(() => { (window as unknown as { sourceSnapshot: object }).sourceSnapshot = { status: 'syncing', last_sync: '2026-10-31T18:00:00Z', count: 2 } })
  await page.clock.fastForward(60_010)
  await expect.poll(() => page.evaluate(() => (window as unknown as { genreReads: number }).genreReads)).toBe(baseline + 1)
  await page.getByRole('button', { name: 'Genre: All genres', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Comedy (2)', exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await page.evaluate(() => { (window as unknown as { sourceSnapshot: object }).sourceSnapshot = { status: 'ok', last_sync: '2026-10-31T19:00:00Z', count: 2 } })
  await page.clock.fastForward(5_010)
  await expect.poll(() => page.evaluate(() => (window as unknown as { genreReads: number }).genreReads)).toBe(baseline + 2)
  const stable = await page.evaluate(() => ({ sources: (window as unknown as { sourceReads: number }).sourceReads, genres: (window as unknown as { genreReads: number }).genreReads }))
  await page.clock.fastForward(60_010)
  await expect.poll(() => page.evaluate(() => (window as unknown as { sourceReads: number }).sourceReads)).toBe(stable.sources + 1)
  expect(await page.evaluate(() => (window as unknown as { genreReads: number }).genreReads)).toBe(stable.genres)
})

test('same-event clear and language updates preserve both changes in saved Live filters', async ({ page }) => {
  await page.addInitScript(() => {
    sessionStorage.setItem('mock.prefs.p2', JSON.stringify({ unrelated: 'retained', 'liveFilters.v1': { language: 'eng', country: 'US', group: 'restricted', source: 's_xt', favorites: true } }))
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const req = new Request(input, init)
      if (new URL(req.url).pathname === '/api/v1/channels') return Response.json({ list_id: 'none', channels: [], total: 0, limit: 80, offset: 0 })
      return original(req)
    }
  })
  await signIn(page)
  await page.getByRole('link', { name: 'Live TV', exact: true }).click()
  await expect(page.getByText('No channels match', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'All languages', exact: true }).last().click()
  await expect.poll(() => page.evaluate(() => JSON.parse(sessionStorage.getItem('mock.prefs.p2') ?? '{}'))).toEqual({ unrelated: 'retained', 'liveFilters.v1': { language: 'all', country: 'US', group: '', source: '', favorites: false } })
})
