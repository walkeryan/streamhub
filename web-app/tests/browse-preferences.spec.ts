import { expect, test } from '@playwright/test'
import { activeFilter, openFilters, signIn } from './helpers'

import { backend, type Document } from './profilePrefsFixture'

const namespace = 'browseFilters.v1.movies'
const defaults = { source: '', sort: 'added', order: '', language: 'mine', category: '', genre: '' }

test('genre and filters persist across reload and another device without replacing unrelated prefs', async ({ page, browser }) => {
  const documents: Record<string, Document> = { p2: { unrelated: { theme: 'keep-me' } } }
  await backend(page, documents)
  await signIn(page)
  await page.getByRole('link', { name: 'Movies', exact: true }).click()
  await openFilters(page)
  await page.getByRole('button', { name: 'Genre: All genres', exact: true }).click()
  await page.getByRole('button', { name: 'Drama (8)', exact: true }).click()
  await page.getByRole('button', { name: 'Source: All sources', exact: true }).click()
  await page.getByRole('button', { name: 'Den Plex', exact: true }).click()
  await page.getByRole('button', { name: 'Release date', exact: true }).click()
  await page.getByRole('button', { name: 'All languages', exact: true }).first().click()
  await expect.poll(() => documents.p2[namespace]).toEqual({ ...defaults, source: 's_plex', genre: 'drama', sort: 'release', language: 'all' })
  expect(documents.p2.unrelated).toEqual({ theme: 'keep-me' })
  await page.reload()
  await expect(activeFilter(page, 'Genre: Drama')).toBeVisible()
  await expect(activeFilter(page, 'Sort: Release date')).toBeVisible()
  await openFilters(page)
  await expect(page.getByRole('button', { name: 'Genre: Drama', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Release date', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.screenshot({ path: '/tmp/streamhub-browse-saved-filters.png' })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  const context = await browser.newContext()
  const other = await context.newPage()
  await backend(other, documents)
  await signIn(other)
  await other.getByRole('link', { name: 'Movies', exact: true }).click()
  await expect(activeFilter(other, 'Language: All languages')).toBeVisible()
  await openFilters(other)
  await expect(other.getByRole('button', { name: 'Genre: Drama', exact: true })).toBeVisible()
  await expect(other.getByRole('button', { name: 'Source: Den Plex', exact: true })).toBeVisible()
  await expect(other.getByRole('button', { name: 'All languages', exact: true }).first()).toHaveAttribute('aria-pressed', 'true')
  await other.getByTestId('browse-filters').getByRole('button', { name: 'Clear filters', exact: true }).click()
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
  await expect.poll(() => pending).toBe(true)
  await page.evaluate(async () => {
    await fetch('/api/v1/disclaimer/accept', { method: 'POST', headers: { authorization: 'Bearer mock-access-p1' } })
    const path = '/src/store/session.ts', fixtures = '/src/mocks/fixtures.ts'
    const { useSession } = await import(/* @vite-ignore */ path)
    const { profiles, DISCLAIMER } = await import(/* @vite-ignore */ fixtures)
    useSession.getState().setSession('mock-access-p1', 'mock-refresh-p1', { ...profiles[0], disclaimer_accepted_version: DISCLAIMER.version })
  })
  await expect(activeFilter(page, 'Genre: Comedy')).toBeVisible()
  release()
  await expect(activeFilter(page, 'Genre: Drama')).toHaveCount(0)
  await openFilters(page)
  await expect(page.getByRole('button', { name: 'Genre: Comedy', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Release date', exact: true }).click()
  await expect.poll(() => documents.p1[namespace]).toEqual({ ...defaults, genre: 'comedy', sort: 'release' })
  expect(documents.p2[namespace]).toEqual({ ...defaults, genre: 'drama' })
})


test('switching profiles cancels a queued preference write before dispatch', async ({ page }) => {
  const documents: Record<string, Document> = { p2: { [namespace]: defaults }, p1: { [namespace]: defaults } }
  let armed = false, blocked = false
  const releases: Array<() => void> = []
  await backend(page, documents, async (id, method) => {
    if (method === 'PATCH' && id === 'p2' && armed) { blocked = true; await new Promise<void>((resolve) => { releases.push(resolve) }) }
  })
  await signIn(page)
  await page.getByRole('link', { name: 'Movies', exact: true }).click()
  await openFilters(page)
  await expect(page.getByRole('button', { name: 'Recently added', exact: true })).toHaveAttribute('aria-pressed', 'true')
  // Hold the dispatched write and queue a second edit while it is in flight.
  armed = true
  await page.getByRole('button', { name: 'Release date', exact: true }).click()
  await expect.poll(() => blocked).toBe(true)
  await page.getByRole('button', { name: 'Title', exact: true }).click()
  expect(documents.p2[namespace]).toEqual(defaults)
  await page.evaluate(async () => {
    await fetch('/api/v1/disclaimer/accept', { method: 'POST', headers: { authorization: 'Bearer mock-access-p1' } })
    const path = '/src/store/session.ts', fixtures = '/src/mocks/fixtures.ts'
    const { useSession } = await import(/* @vite-ignore */ path)
    const { profiles, DISCLAIMER } = await import(/* @vite-ignore */ fixtures)
    useSession.getState().setSession('mock-access-p1', 'mock-refresh-p1', { ...profiles[0], disclaimer_accepted_version: DISCLAIMER.version })
  })
  // The new profile's screen starts with its own (default) filters and a closed panel.
  await expect(page.getByTestId('browse-filters')).toHaveCount(0)
  await expect(page.getByTestId('active-filter')).toHaveCount(0)
  await openFilters(page)
  await expect(page.getByRole('button', { name: 'Recently added', exact: true })).toHaveAttribute('aria-pressed', 'true')
  armed = false
  for (const release of releases) release()
  await page.getByRole('button', { name: 'Title', exact: true }).click()
  await expect.poll(() => documents.p1[namespace]).toEqual({ ...defaults, sort: 'title' })
  expect(documents.p2[namespace]).toEqual({ ...defaults, sort: 'release' })
})
