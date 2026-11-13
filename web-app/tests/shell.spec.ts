import { expect, test, type Page } from '@playwright/test'
import { openMore } from './helpers'

// Inside the Android TV app (a fake window.StreamHubTV here, added before the page loads, as the TV's
// WebView does) the web app is the whole interface and the TV's own player plays.
const FILM = 'tmdb:movie:110'

declare global {
  interface Window { streamhubShell?: { back: () => boolean; token: (id: string, rejected: string | null) => void; playbackEnded: () => void } }
}

type Shell = { plays: string[]; signedOut: string[]; tokens: Array<{ id: string; json: string }>; accept: boolean }
const shellState = (page: Page) => page.evaluate(() => (window as unknown as { __shell: Shell }).__shell)

async function enter(page: Page, { bridge = true, session = true }: { bridge?: boolean; session?: boolean } = {}) {
  const resolves: string[] = []
  page.on('request', (r) => { if (new URL(r.url()).pathname.endsWith('/playback/resolve')) resolves.push(r.url()) })
  await page.addInitScript(({ bridge, session }) => {
    const profile = { id: 'p2', name: 'Guest', avatar: null, is_admin: false, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' }
    sessionStorage.setItem('mock.accepted', JSON.stringify(['p1', 'p2', 'p3']))
    if (session && !localStorage.getItem('sh.session.v1')) localStorage.setItem('sh.session.v1', JSON.stringify({ access: 'mock-access-p2', refresh: 'mock-refresh-p2', profile }))
    if (!bridge) return
    const state: Shell = { plays: [], signedOut: [], tokens: [], accept: sessionStorage.getItem('test.refuse') !== '1' }
    const w = window as unknown as { __shell: Shell; StreamHubTV: unknown }
    w.__shell = state
    w.StreamHubTV = {
      play: (json: string) => { state.plays.push(json); return state.accept },
      signedOut: (reason: string) => { state.signedOut.push(reason) },
      capabilities: () => JSON.stringify({ bridge: 1, shell: '1.2.0', native_playback: true }),
      deliverToken: (id: string, json: string) => { state.tokens.push({ id, json }) },
    }
  }, { bridge, session })
  return resolves
}

test('Play hands the title to the TV with what the web player would have used, and no web player starts', async ({ page }) => {
  const resolves = await enter(page)
  await page.goto(`/title/${encodeURIComponent(FILM)}`)
  await expect(page.getByTestId('title-play')).toBeVisible()
  await expect(page.locator('html')).toHaveAttribute('data-shell', 'android-tv')
  await page.getByTestId('title-play').click()
  await expect.poll(async () => (await shellState(page)).plays.length).toBe(1)
  const play = JSON.parse((await shellState(page)).plays[0]!)
  expect(play).toMatchObject({ v: 1, kind: 'title', id: FILM })
  expect(play.url).toBeUndefined()
  // The page stays where it was: same screen, the Play button still focused, no player, no resolve of its own.
  await expect(page).toHaveURL(new RegExp(`/title/${encodeURIComponent(FILM)}`))
  await expect(page.getByTestId('player')).toHaveCount(0)
  await expect(page.getByTestId('title-play')).toHaveAttribute('data-focused', 'true')
  expect(resolves).toEqual([])
  // The TV's player closed: the page reads progress again and is still on the same screen.
  await page.evaluate(() => window.streamhubShell!.playbackEnded())
  await expect(page).toHaveURL(new RegExp(`/title/${encodeURIComponent(FILM)}`))
})

test('a player address reached directly is handed over too, with its choices, and the page goes back', async ({ page }) => {
  const resolves = await enter(page)
  await page.goto('/')
  await expect(page.getByTestId('home')).toBeVisible()
  await page.goto(`/play/${encodeURIComponent('ch:1')}?catchup=${encodeURIComponent('prog:9')}`)
  await expect.poll(async () => (await shellState(page)).plays.length).toBe(1)
  expect(JSON.parse((await shellState(page)).plays[0]!)).toEqual({ v: 1, kind: 'title', id: 'ch:1', catchup_programme_id: 'prog:9' })
  await expect(page.getByTestId('player')).toHaveCount(0)
  await expect(page).not.toHaveURL(/\/play\//)
  expect(resolves).toEqual([])
})

test('a refused hand-off is said, and still no web player starts', async ({ page }) => {
  await enter(page)
  await page.goto('/')
  await page.evaluate(() => sessionStorage.setItem('test.refuse', '1'))
  await page.goto(`/title/${encodeURIComponent(FILM)}`)
  await page.getByTestId('title-play').click()
  await expect(page.getByText('The TV could not start this.')).toBeVisible()
  await expect(page.getByTestId('player')).toHaveCount(0)
})

test('Back from the TV remote closes an open dialog first, goes back next, and says no on the home screen', async ({ page }) => {
  await enter(page)
  await page.goto('/')
  await expect(page.getByTestId('home')).toBeVisible()
  await page.getByTestId('nav-more').click()
  await expect(page.getByTestId('more-menu')).toBeVisible()
  expect(await page.evaluate(() => window.streamhubShell!.back())).toBe(true)
  await expect(page.getByTestId('more-menu')).toHaveCount(0)

  await openMore(page, 'Settings')
  await expect(page).toHaveURL(/\/settings/)
  // Focus may still be in the top bar; Back walks out of it, then back to Home.
  for (let i = 0; i < 4 && !/\/$/.test(new URL(page.url()).pathname); i++) expect(await page.evaluate(() => window.streamhubShell!.back())).toBe(true)
  await expect(page.getByTestId('home')).toBeVisible()
  await page.keyboard.press('ArrowDown')
  // Nothing left to close or go back to: the TV leaves the app.
  let answer = true
  for (let i = 0; i < 3 && answer; i++) answer = await page.evaluate(() => window.streamhubShell!.back())
  expect(answer).toBe(false)
  await expect(page.getByTestId('home')).toBeVisible()
})

test('the TV asks for a token: it gets the current one, and a fresh one for a token the server refused', async ({ page }) => {
  await enter(page)
  const refreshes: string[] = []
  page.on('request', (r) => { if (new URL(r.url()).pathname.endsWith('/auth/refresh')) refreshes.push(r.postData() ?? '') })
  await page.goto('/')
  await expect(page.getByTestId('home')).toBeVisible()
  await page.evaluate(() => window.streamhubShell!.token('a'.repeat(32), null))
  await expect.poll(async () => (await shellState(page)).tokens.length).toBe(1)
  const first = (await shellState(page)).tokens[0]!
  expect(first.id).toBe('a'.repeat(32))
  const current = JSON.parse(first.json)
  expect(current.profile.id).toBe('p2')
  expect(typeof current.access).toBe('string')
  expect(refreshes).toEqual([])
  await page.evaluate((rejected) => window.streamhubShell!.token('b'.repeat(32), rejected), current.access)
  await expect.poll(async () => (await shellState(page)).tokens.length).toBe(2)
  const second = JSON.parse((await shellState(page)).tokens[1]!.json)
  // The page refreshed (it alone holds the refresh token) and answers with the pair it now keeps.
  expect(refreshes).toHaveLength(1)
  expect(second.profile.id).toBe('p2')
  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('sh.session.v1')!))
  expect(stored.access).toBe(second.access)
})

test('a page with no session hands the TV back to its pairing screen', async ({ page }) => {
  await enter(page, { session: false })
  await page.goto('/')
  await expect.poll(async () => (await shellState(page)).signedOut.length).toBeGreaterThan(0)
})

test('an ordinary browser is untouched: no shell, and Play opens the web player', async ({ page }) => {
  const resolves = await enter(page, { bridge: false })
  await page.goto(`/play/${encodeURIComponent(FILM)}`)
  await expect(page.getByTestId('player')).toBeVisible()
  await expect.poll(() => resolves.length).toBeGreaterThan(0)
  expect(await page.evaluate(() => ({ shell: document.documentElement.dataset.shell ?? null, bridge: typeof window.streamhubShell }))).toEqual({ shell: null, bridge: 'undefined' })
  expect(await page.locator('meta[name="viewport"]').getAttribute('content')).toBe('width=device-width, initial-scale=1, viewport-fit=cover')
})

test.describe('on a TV WebView that reports 960×540 CSS pixels', () => {
  test.use({ viewport: { width: 960, height: 540 }, deviceScaleFactor: 2, isMobile: true, hasTouch: false })

  test('the shell lays out what a 1920×1080 desktop browser shows', async ({ page }) => {
    await enter(page)
    await page.goto('/')
    await expect(page.getByTestId('home')).toBeVisible()
    await expect.poll(() => page.evaluate(() => window.innerWidth)).toBe(1920)
    // The wide layout: the top bar with its search box, not the phone's bottom bar.
    await expect(page.getByRole('textbox', { name: 'Search', exact: true })).toBeVisible()
    expect(await page.evaluate(() => getComputedStyle(document.documentElement).fontSize)).toBe('24px')
  })

  test('without the bridge the same screen keeps its own width', async ({ page }) => {
    await enter(page, { bridge: false })
    await page.goto('/')
    await expect(page.getByTestId('home')).toBeVisible()
    expect(await page.evaluate(() => window.innerWidth)).toBe(960)
  })
})
