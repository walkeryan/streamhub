import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('late profile results and local preferences cannot cross a profile switch', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('sh.homePrefs.v1', JSON.stringify({ order: ['favorites'], hidden: ['news'] }))
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const request = new Request(input, init)
      if (new URL(request.url).pathname === '/api/v1/favorites') {
        const guest = request.headers.get('authorization') === 'Bearer mock-access-p2'
        const state = window as unknown as { releaseGuest?: () => void; guestPending?: boolean }
        if (guest) {
          state.guestPending = true
          await new Promise<void>((resolve) => { state.releaseGuest = resolve })
        }
        return Response.json([{ id: guest ? 'old-profile' : 'new-profile', kind: 'movie', title: guest ? 'Guest private favorite' : 'Ryan private favorite', sources: [], is_favorite: true }])
      }
      return original(request)
    }
  })
  await signIn(page)
  await page.evaluate(async () => {
    const path = '/src/store/ui.ts', fixtures = '/src/mocks/fixtures.ts'
    const { useUi } = await import(/* @vite-ignore */ path)
    const { channels } = await import(/* @vite-ignore */ fixtures)
    useUi.getState().addSearch('Guest private query')
    useUi.getState().setSearchQuery('Guest private query')
    useUi.getState().setChannelList('guest-list', [channels[0]])
    useUi.getState().setLastChannelId(channels[0].id)
  })
  await page.getByRole('link', { name: 'Favorites', exact: true }).click()
  await expect.poll(() => page.evaluate(() => (window as unknown as { guestPending?: boolean }).guestPending)).toBe(true)
  await page.evaluate(async () => {
    await fetch('/api/v1/disclaimer/accept', { method: 'POST', headers: { authorization: 'Bearer mock-access-p1' } })
    const path = '/src/store/session.ts', fixtures = '/src/mocks/fixtures.ts'
    const { useSession } = await import(/* @vite-ignore */ path)
    const { profiles, DISCLAIMER } = await import(/* @vite-ignore */ fixtures)
    useSession.getState().setSession('mock-access-p1', 'mock-refresh-p1', { ...profiles[0], disclaimer_accepted_version: DISCLAIMER.version })
  })
  await expect(page.getByRole('link', { name: 'Ryan private favorite', exact: true })).toBeVisible()
  await page.evaluate(() => (window as unknown as { releaseGuest: () => void }).releaseGuest())
  await expect(page.getByRole('link', { name: 'Guest private favorite', exact: true })).toHaveCount(0)
  const state = await page.evaluate(async () => {
    const path = '/src/store/ui.ts'
    const { useUi } = await import(/* @vite-ignore */ path)
    const s = useUi.getState()
    return { recent: s.recentSearches, query: s.searchQuery, list: s.channelList.channels.length, last: s.lastChannelId, home: s.homePrefs }
  })
  expect(state).toEqual({ recent: [], query: '', list: 0, last: null, home: { order: [], hidden: [] } })
  const restored = await page.evaluate(async () => {
    const path = '/src/store/session.ts', ui = '/src/store/ui.ts', fixtures = '/src/mocks/fixtures.ts'
    const { useSession } = await import(/* @vite-ignore */ path)
    const { useUi } = await import(/* @vite-ignore */ ui)
    const { profiles, DISCLAIMER } = await import(/* @vite-ignore */ fixtures)
    useSession.getState().setSession('mock-access-p2', 'mock-refresh-p2', { ...profiles[1], disclaimer_accepted_version: DISCLAIMER.version })
    return { recent: useUi.getState().recentSearches, home: useUi.getState().homePrefs, legacy: localStorage.getItem('sh.homePrefs.v1') }
  })
  expect(restored.recent).toEqual(['Guest private query'])
  expect(restored.home).toEqual({ order: ['favorites'], hidden: ['news'] })
  expect(JSON.parse(restored.legacy!)).toEqual(restored.home)
})


test('new profiles default to English while existing profile preferences remain unchanged', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('sh.session.v1', JSON.stringify({ access: 'mock-access-p1', refresh: 'mock-refresh-p1', profile: { id: 'p1', name: 'Ryan' } }))
    sessionStorage.setItem('mock.accepted', JSON.stringify(['p1']))
  })
  await page.goto('/settings?tab=profiles')
  await page.getByLabel('Profile name', { exact: true }).fill('Party guest')
  await page.getByRole('button', { name: 'Add profile', exact: true }).click()
  await expect.poll(() => page.evaluate(async () => {
    const path = '/src/mocks/fixtures.ts'
    return (await import(/* @vite-ignore */ path)).profiles.find((p: {name: string}) => p.name === 'Party guest')?.languages
  })).toEqual(['eng'])
  const profiles = await page.evaluate(async () => {
    const path = '/src/mocks/fixtures.ts'
    return (await import(/* @vite-ignore */ path)).profiles
  })
  expect(profiles.find((p: { id: string }) => p.id === 'p2').languages).toEqual(['eng', 'spa'])
})
