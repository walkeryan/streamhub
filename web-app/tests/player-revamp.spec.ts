import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

/*
 * The revamped player (docs/specs/2026-11-18-title-and-player-design.md, section 2) against the mock
 * episodes: tmdb:episode:204:<season>:<n>, a 30 s clip with an intro at 1–6 s and credits from
 * `mock.credits_at` (21 s unless a test moves it), two seasons of six.
 */
const EP = (season: number, n: number) => `tmdb:episode:204:${season}:${n}`
const playUrl = (id: string) => `/play/${encodeURIComponent(id)}`

async function open(page: Page, id: string, knobs: Record<string, string> = {}) {
  await signIn(page)
  await page.evaluate((k) => { for (const [key, value] of Object.entries(k)) sessionStorage.setItem(key, value) }, knobs)
  await page.goto(playUrl(id))
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', /playing|paused|buffering/)
}
const position = (page: Page) => page.getByTestId('video').evaluate((v: HTMLVideoElement) => v.currentTime)
const seekTo = (page: Page, s: number) => page.getByTestId('video').evaluate((v: HTMLVideoElement, t) => { v.currentTime = t; void v.play() }, s)

test('an episode shows its show, season and episode over its title, and the new controls', async ({ page }) => {
  await open(page, EP(2, 1))
  await expect(page.getByTestId('player-line')).toHaveText(/ · Season 2 · Episode 1$/)
  await expect(page.getByTestId('player-title')).toHaveText('Landfall')
  const bar = page.getByTestId('player-bar')
  for (const name of ['Audio', 'Subtitles', 'Previous episode', 'Back 10 seconds', 'Forward 30 seconds', 'Next episode', 'Watch together', 'Settings']) {
    await expect(bar.getByRole('button', { name, exact: true })).toBeVisible()
  }
  await expect(page.getByTestId('player-subs-value')).toHaveText('Off')
  // Intro and credits are marked on the timeline.
  await expect(page.getByTestId('scrub-mark')).toHaveCount(2)
  // A pointer over the timeline shows a preview frame of that place, named by its position.
  const track = page.getByTestId('scrub-track')
  const box = (await track.boundingBox())!
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height / 2)
  await expect(page.getByTestId('scrub-preview')).toHaveAttribute('src', /\/thumbnails\/mock-204-2-1\?ms=14000$/)
})

test('Skip intro shows during the intro and jumps to its end', async ({ page }) => {
  await open(page, EP(1, 2))
  await seekTo(page, 1.5)
  const skip = page.getByTestId('skip-intro')
  await expect(skip).toBeVisible()
  await skip.click()
  await expect.poll(() => position(page)).toBeGreaterThanOrEqual(5.9)
  await expect(skip).toHaveCount(0)
})

test('OK with the controls hidden takes the skip that is offered', async ({ page }) => {
  await open(page, EP(1, 2))
  await seekTo(page, 1.2)
  await expect(page.getByTestId('skip-intro')).toBeVisible()
  await expect(page.getByTestId('player-ui')).toHaveAttribute('data-show', 'false', { timeout: 8000 })
  if (!(await page.getByTestId('skip-intro').count())) await seekTo(page, 1.2)
  await page.keyboard.press('Enter')
  await expect.poll(() => position(page)).toBeGreaterThanOrEqual(5.9)
})

test('Up next comes in at the credits, counts down from 10 and plays the next episode in the same player', async ({ page }) => {
  await open(page, EP(1, 6), { 'mock.credits_at': '3000' })
  const card = page.getByTestId('up-next')
  await expect(card).toBeVisible({ timeout: 8000 })
  await expect(card).toContainText('Up next · S2 · E1')
  await expect(page.getByTestId('up-next-title')).toHaveText('Landfall')
  await expect(page.getByTestId('skip-credits')).toBeVisible()
  const first = Number(await page.getByTestId('up-next-count').textContent())
  expect(first).toBeGreaterThanOrEqual(6)
  await expect.poll(async () => Number(await page.getByTestId('up-next-count').textContent())).toBeLessThan(first)
  // The same player moves on: the address follows, and the new episode's titles show.
  await expect(page).toHaveURL(new RegExp(encodeURIComponent(EP(2, 1)).replace(/%/g, '%')), { timeout: 14_000 })
  await expect(page.getByTestId('player-line')).toHaveText(/Season 2 · Episode 1$/)
  await expect(card).toHaveCount(0)
})

test('Skip credits plays the next episode now; Keep watching and Back put the card away until the end', async ({ page }) => {
  await open(page, EP(1, 1), { 'mock.credits_at': '2000' })
  await expect(page.getByTestId('up-next')).toBeVisible({ timeout: 8000 })
  await page.getByTestId('up-next-keep').click()
  await expect(page.getByTestId('up-next')).toHaveCount(0)
  // Dismissed, it stays away while the credits play…
  await page.waitForTimeout(1500)
  await expect(page.getByTestId('up-next')).toHaveCount(0)
  // …and comes back at the very end with a fresh countdown.
  await seekTo(page, 29.4)
  await expect(page.getByTestId('up-next')).toBeVisible({ timeout: 5000 })
  await expect(page.getByTestId('up-next-count')).toHaveText(/^(10|9)$/)
  await page.getByTestId('skip-credits').click()
  await expect(page).toHaveURL(/tmdb%3Aepisode%3A204%3A1%3A2/)
  await expect(page.getByTestId('player-title')).toHaveText('Slack Water')
})

test('Escape dismisses the card without leaving the player', async ({ page }) => {
  await open(page, EP(1, 3), { 'mock.credits_at': '2000' })
  await expect(page.getByTestId('up-next')).toBeVisible({ timeout: 8000 })
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('up-next')).toHaveCount(0)
  await expect(page.getByTestId('player')).not.toHaveAttribute('data-mini', 'true')
})

test('with autoplay off the card offers Play next and never counts', async ({ page }) => {
  await signIn(page)
  await page.goto('/settings?tab=playback')
  await page.getByTestId('autoplay-off').click()
  await expect(page.getByTestId('autoplay-off')).toHaveAttribute('aria-pressed', 'true')
  await page.evaluate(() => sessionStorage.setItem('mock.credits_at', '2000'))
  await page.goto(playUrl(EP(1, 4)))
  const card = page.getByTestId('up-next')
  await expect(card).toBeVisible({ timeout: 8000 })
  await expect(card).toHaveAttribute('data-autoplay', 'false')
  await expect(page.getByTestId('up-next-count')).toHaveCount(0)
  await page.waitForTimeout(11_000)
  await expect(page).toHaveURL(/tmdb%3Aepisode%3A204%3A1%3A4/)
  await page.getByTestId('up-next-play-next').click()
  await expect(page).toHaveURL(/tmdb%3Aepisode%3A204%3A1%3A5/)
})

test('after three autoplays in a row with no input, the fourth waits on Still watching', async ({ page }) => {
  test.setTimeout(120_000)
  await open(page, EP(1, 1), { 'mock.credits_at': '1000' })
  for (const n of [2, 3, 4]) await expect(page).toHaveURL(new RegExp(`tmdb%3Aepisode%3A204%3A1%3A${n}`), { timeout: 20_000 })
  const still = page.getByTestId('still-watching')
  await expect(still).toBeVisible({ timeout: 20_000 })
  await expect(page).toHaveURL(/tmdb%3Aepisode%3A204%3A1%3A4/)
  expect(await page.getByTestId('video').evaluate((v: HTMLVideoElement) => v.paused)).toBe(true)
  await page.getByTestId('still-continue').click()
  await expect(page).toHaveURL(/tmdb%3Aepisode%3A204%3A1%3A5/)
  await expect(still).toHaveCount(0)
})

test('a film\'s Skip credits ends it and returns', async ({ page }) => {
  await signIn(page)
  await page.evaluate(() => sessionStorage.setItem('mock.film_credits', '2000'))
  await page.getByTestId('hero-play').first().click()
  await expect(page.getByTestId('player')).toBeVisible()
  const skip = page.getByTestId('skip-credits')
  await expect(skip).toBeVisible({ timeout: 8000 })
  await expect(page.getByTestId('up-next')).toHaveCount(0)
  await skip.click()
  await expect(page.getByTestId('player')).toHaveCount(0)
  await expect(page.getByTestId('home')).toBeVisible()
})

test('minimised, the same playback carries on as a bar while browsing; expand and close', async ({ page }) => {
  await open(page, EP(1, 2))
  await seekTo(page, 8)
  await page.getByTestId('player-minimise').click()
  const mini = page.getByTestId('mini-player')
  await expect(mini).toBeVisible()
  await expect(page.getByTestId('player')).toHaveAttribute('data-mini', 'true')
  await expect(page.getByTestId('mini-title')).not.toHaveText('')
  await expect(mini).toContainText('S1 · E2 · Slack Water')
  // The page under it works, and playback has not restarted.
  await page.getByTestId('nav-home').click()
  await expect(page.getByTestId('home')).toBeVisible()
  await expect.poll(() => position(page)).toBeGreaterThan(8)
  const before = await position(page)
  await page.getByTestId('mini-play').click()
  expect(await page.getByTestId('video').evaluate((v: HTMLVideoElement) => v.paused)).toBe(true)
  await page.getByTestId('mini-play').click()
  await page.getByTestId('mini-expand').click()
  await expect(page).toHaveURL(/\/play\/tmdb%3Aepisode%3A204%3A1%3A2/)
  await expect(page.getByTestId('player')).not.toHaveAttribute('data-mini', 'true')
  expect(await position(page)).toBeGreaterThanOrEqual(before)
  // Back while it plays minimises again; Close ends it.
  // (The first Back hides the controls if they show; a later one minimises.)
  await expect.poll(async () => {
    await page.keyboard.press('Escape')
    return page.getByTestId('player').getAttribute('data-mini')
  }, { intervals: [300] }).toBe('true')
  await page.getByTestId('mini-close').click()
  await expect(page.getByTestId('player')).toHaveCount(0)
  await expect(mini).toHaveCount(0)
})

test('starting another title replaces the mini player', async ({ page }) => {
  await open(page, EP(1, 2))
  await page.getByTestId('player-minimise').click()
  await expect(page.getByTestId('mini-player')).toBeVisible()
  await page.goto(playUrl(EP(2, 3)))
  await expect(page.getByTestId('mini-player')).toHaveCount(0)
  await expect(page.getByTestId('player-title')).toHaveText('What the Net Held')
})

test.describe('phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true })
  test('the mini player is one compact row above the navigation bar', async ({ page }) => {
    await open(page, EP(1, 2))
    await page.getByTestId('player-minimise').click()
    const mini = page.getByTestId('mini-player')
    await expect(mini).toBeVisible()
    await expect(page.getByTestId('mini-play')).toBeVisible()
    await expect(page.getByTestId('mini-close')).toBeVisible()
    await expect(page.getByTestId('mini-expand')).toHaveCount(0)
    const bar = (await mini.boundingBox())!
    const rail = (await page.getByTestId('rail').boundingBox())!
    expect(bar.y + bar.height).toBeLessThanOrEqual(rail.y + 1)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  })
})

test('inside the TV app, the bar follows the TV player in picture-in-picture and drives it', async ({ page }) => {
  await page.addInitScript(() => {
    const sent: string[] = []
    ;(window as unknown as { __sent: string[] }).__sent = sent
    ;(window as unknown as { StreamHubTV: object }).StreamHubTV = {
      play: () => true, signedOut: () => {}, capabilities: () => '{}', deliverToken: () => {},
      message: (json: string) => { sent.push(json); return true },
    }
  })
  await signIn(page)
  const state = { id: EP(1, 2), title: 'Fixture Show', subtitle: 'S1 · E2 · Slack Water', image: null, position_ms: 61_000, duration_ms: 3_000_000, playing: true, has_next: true, has_previous: true, live: false }
  expect(await page.evaluate((s) => window.streamhubShell!.message(JSON.stringify({ v: 1, kind: 'mini_player', state: s })), state)).toBe(true)
  const bar = page.getByTestId('shell-mini-player')
  await expect(bar).toBeVisible()
  await expect(bar).toContainText('Fixture Show')
  await expect(bar).toContainText('S1 · E2 · Slack Water')
  await page.getByTestId('mini-fwd30').click()
  await page.getByTestId('mini-next').click()
  await page.getByTestId('mini-expand').click()
  const sent = await page.evaluate(() => (window as unknown as { __sent: string[] }).__sent.map((j) => JSON.parse(j)))
  expect(sent).toEqual([
    { v: 1, kind: 'mini_player_command', command: 'seek_by', delta_ms: 30_000 },
    { v: 1, kind: 'mini_player_command', command: 'next', delta_ms: null },
    { v: 1, kind: 'mini_player_command', command: 'expand', delta_ms: null },
  ])
  await page.evaluate(() => window.streamhubShell!.message('{"v":1,"kind":"mini_player","state":null}'))
  await expect(bar).toHaveCount(0)
})
