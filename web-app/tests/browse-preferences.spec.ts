import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

type Document = Record<string, unknown>
async function backend(page: Page, documents: Record<string, Document>, beforeRead?: (id: string) => Promise<void>) {
  await page.exposeFunction('prefsBackend', async (id: string, method: string, body: Document | null) => {
    if (method === 'PUT') documents[id] = body!
    else await beforeRead?.(id)
    return documents[id] ?? {}
  })
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const req = new Request(input, init)
      const match = new URL(req.url).pathname.match(/^\/api\/v1\/profiles\/([^/]+)\/prefs$/)
      if (match) {
        const state = window as unknown as { prefsBackend: (id: string, method: string, body: Record<string, unknown> | null) => Promise<Record<string, unknown>> }
        return Response.json(await state.prefsBackend(match[1], req.method, req.method === 'PUT' ? await req.json() : null))
      }
      return original(req)
    }
  })
}
const namespace = 'browseFilters.v1.movies'
const defaults = { source: '', sort: 'added', language: 'mine', category: '', genre: '' }

test('genre and filters persist across reload and another device without replacing unrelated prefs', async ({ page, browser }) => {
  const documents: Record<string, Document> = { p2: { unrelated: { theme: 'keep-me' } } }
  await backend(page, documents)
  await signIn(page)
  await page.getByRole('link', { name: 'Movies', exact: true }).click()
  await expect(page.getByTestId('discover')).toBeVisible()
  await page.getByRole('button', { name: 'My library', exact: true }).click()
  await page.getByRole('button', { name: 'Genre: All genres', exact: true }).click()
  await page.getByRole('button', { name: 'Drama (8)', exact: true }).click()
  await page.getByRole('button', { name: 'Source: All sources', exact: true }).click()
  await page.getByRole('button', { name: 'Den Plex', exact: true }).click()
  await page.getByRole('button', { name: 'Year', exact: true }).click()
  await page.getByRole('button', { name: 'All languages', exact: true }).first().click()
  await expect.poll(() => documents.p2[namespace]).toEqual({ ...defaults, source: 's_plex', genre: 'drama', sort: 'year', language: 'all' })
  expect(documents.p2.unrelated).toEqual({ theme: 'keep-me' })
  await page.reload()
  await expect(page.getByRole('button', { name: 'Genre: Drama', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Year', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.screenshot({ path: '/tmp/streamhub-browse-saved-filters.png' })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  const context = await browser.newContext()
  const other = await context.newPage()
  await backend(other, documents)
  await signIn(other)
  await other.getByRole('link', { name: 'Movies', exact: true }).click()
  await expect(other.getByTestId('discover')).toBeVisible()
  await other.getByRole('button', { name: 'My library', exact: true }).click()
  await expect(other.getByRole('button', { name: 'Genre: Drama', exact: true })).toBeVisible()
  await expect(other.getByRole('button', { name: 'Source: Den Plex', exact: true })).toBeVisible()
  await expect(other.getByRole('button', { name: 'All languages', exact: true }).first()).toHaveAttribute('aria-pressed', 'true')
  await other.getByRole('button', { name: 'Clear filters', exact: true }).first().click()
  await expect.poll(() => documents.p2[namespace]).toEqual(defaults)
  expect(documents.p2.unrelated).toEqual({ theme: 'keep-me' })
  await context.close()
})

test('delayed previous-profile preferences never appear after switching profiles', async ({ page }) => {
  const documents: Record<string, Document> = { p2: { [namespace]: { ...defaults, genre: 'drama' } }, p1: { [namespace]: { ...defaults, genre: 'comedy' } } }
  let release: () => void = () => undefined
  let pending = false
  await backend(page, documents, async (id) => {
    if (id === 'p2') { pending = true; await new Promise<void>((resolve) => { release = resolve }) }
  })
  await signIn(page)
  await page.getByRole('link', { name: 'Movies', exact: true }).click()
  await expect(page.getByTestId('discover')).toBeVisible()
  await page.getByRole('button', { name: 'My library', exact: true }).click()
  await expect.poll(() => pending).toBe(true)
  await page.evaluate(async () => {
    await fetch('/api/v1/disclaimer/accept', { method: 'POST', headers: { authorization: 'Bearer mock-access-p1' } })
    const path = '/src/store/session.ts', fixtures = '/src/mocks/fixtures.ts'
    const { useSession } = await import(/* @vite-ignore */ path)
    const { profiles, DISCLAIMER } = await import(/* @vite-ignore */ fixtures)
    useSession.getState().setSession('mock-access-p1', 'mock-refresh-p1', { ...profiles[0], disclaimer_accepted_version: DISCLAIMER.version })
  })
  await expect(page.getByRole('button', { name: 'Genre: Comedy', exact: true })).toBeVisible()
  release()
  await expect(page.getByRole('button', { name: 'Genre: Drama', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Year', exact: true }).click()
  await expect.poll(() => documents.p1[namespace]).toEqual({ ...defaults, genre: 'comedy', sort: 'year' })
  expect(documents.p2[namespace]).toEqual({ ...defaults, genre: 'drama' })
})


test('switching profiles cancels a queued preference write before PUT', async ({ page }) => {
  const documents: Record<string, Document> = { p2: { [namespace]: defaults }, p1: { [namespace]: defaults } }
  let reads = 0, blocked = false
  let release: () => void = () => undefined
  await backend(page, documents, async (id) => {
    if (id === 'p2' && ++reads === 2) { blocked = true; await new Promise<void>((resolve) => { release = resolve }) }
  })
  await signIn(page)
  await page.getByRole('link', { name: 'Movies', exact: true }).click()
  await expect(page.getByTestId('discover')).toBeVisible()
  await page.getByRole('button', { name: 'My library', exact: true }).click()
  await page.getByRole('button', { name: 'Year', exact: true }).click()
  await expect.poll(() => blocked).toBe(true)
  await page.evaluate(async () => {
    await fetch('/api/v1/disclaimer/accept', { method: 'POST', headers: { authorization: 'Bearer mock-access-p1' } })
    const path = '/src/store/session.ts', fixtures = '/src/mocks/fixtures.ts'
    const { useSession } = await import(/* @vite-ignore */ path)
    const { profiles, DISCLAIMER } = await import(/* @vite-ignore */ fixtures)
    useSession.getState().setSession('mock-access-p1', 'mock-refresh-p1', { ...profiles[0], disclaimer_accepted_version: DISCLAIMER.version })
  })
  await expect(page.getByRole('button', { name: 'Recently added', exact: true })).toHaveAttribute('aria-pressed', 'true')
  release()
  await page.getByRole('button', { name: 'Title', exact: true }).click()
  await expect.poll(() => documents.p1[namespace]).toEqual({ ...defaults, sort: 'title' })
  expect(documents.p2[namespace]).toEqual(defaults)
})
