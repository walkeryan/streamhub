import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('Sports waits for saved filters, reloads them, and isolates another profile', async ({ page }) => {
  const docs: Record<string, Record<string, unknown>> = {
    p2: { unrelated: 'keep' }, p1: { 'sportsFilters.v1': { language: 'eng', sport: 'Basketball' } },
  }
  const queries: Array<{ profile: string; language: string; sport: string }> = []
  let blockReads = false, pending = false, release: () => void = () => undefined
  await page.exposeFunction('sportsPrefsBackend', async (id: string, method: string, body: Record<string, unknown> | null) => {
    if (method === 'PUT') docs[id] = body!
    else if (blockReads) { pending = true; await new Promise<void>((resolve) => { release = resolve }) }
    return docs[id] ?? {}
  })
  await page.exposeFunction('recordSportsQuery', (query: typeof queries[number]) => queries.push(query))
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const req = new Request(input, init), url = new URL(req.url)
      const match = url.pathname.match(/^\/api\/v1\/profiles\/([^/]+)\/prefs$/)
      const state = window as unknown as {
        sportsPrefsBackend: (id: string, method: string, body: unknown) => Promise<unknown>
        recordSportsQuery: (query: { profile: string; language: string; sport: string }) => Promise<void>
      }
      if (match) return Response.json(await state.sportsPrefsBackend(match[1], req.method, req.method === 'PUT' ? await req.json() : null))
      if (url.pathname === '/api/v1/sports') {
        await state.recordSportsQuery({ profile: req.headers.get('authorization') ?? '', language: url.searchParams.get('language') ?? '', sport: url.searchParams.get('sport') ?? '' })
        const now = Date.now()
        let items = ['Basketball', 'Tennis'].map((sport, i) => ({ id: String(i), sport, programme_id: String(i), title: `${sport} programme`, start: new Date(now - 60000).toISOString(), stop: new Date(now + 600000).toISOString(), channel_id: `ch:${i}`, channel_name: `Channel ${i}`, channel_number: i, source_name: 'Fixture' }))
        const sport = url.searchParams.get('sport')
        if (sport) items = items.filter((item) => item.sport === sport)
        return Response.json({ as_of: new Date(now).toISOString(), items, total: items.length, offset: 0, limit: 40, sports: [{ name: 'Basketball', count: 1 }, { name: 'Tennis', count: 1 }], coverage: { eligible_channels: 2, channels_with_current_schedule: 2 } })
      }
      return original(req)
    }
  })
  await signIn(page)
  await page.getByRole('link', { name: 'Sports', exact: true }).click()
  await page.getByRole('button', { name: 'All languages', exact: true }).click()
  await page.getByRole('button', { name: 'Tennis · 1', exact: true }).click()
  await expect.poll(() => docs.p2['sportsFilters.v1']).toEqual({ language: 'all', sport: 'Tennis' })
  expect(docs.p2.unrelated).toBe('keep')
  blockReads = true
  const before = queries.length
  await page.reload()
  await expect(page.getByText('Loading saved filters…', { exact: true })).toBeVisible()
  await expect.poll(() => pending).toBe(true)
  expect(queries.length).toBe(before)
  blockReads = false
  release()
  await expect(page.getByRole('button', { name: 'Tennis · 1', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('button', { name: 'All languages', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect.poll(() => queries.at(-1)).toEqual({ profile: 'Bearer mock-access-p2', language: 'all', sport: 'Tennis' })
  await page.evaluate(async () => {
    await fetch('/api/v1/disclaimer/accept', { method: 'POST', headers: { authorization: 'Bearer mock-access-p1' } })
    const path = '/src/store/session.ts', fixtures = '/src/mocks/fixtures.ts'
    const { useSession } = await import(/* @vite-ignore */ path)
    const { profiles, DISCLAIMER } = await import(/* @vite-ignore */ fixtures)
    useSession.getState().setSession('mock-access-p1', 'mock-refresh-p1', { ...profiles[0], disclaimer_accepted_version: DISCLAIMER.version })
  })
  await expect(page.getByRole('button', { name: 'Basketball · 1', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('button', { name: 'English', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect.poll(() => queries.at(-1)).toEqual({ profile: 'Bearer mock-access-p1', language: 'eng', sport: 'Basketball' })
  expect(docs.p2['sportsFilters.v1']).toEqual({ language: 'all', sport: 'Tennis' })
})
