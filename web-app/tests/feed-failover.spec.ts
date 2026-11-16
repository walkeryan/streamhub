import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

// Mock line-up: the field hockey game is on now on two feeds (VANTIX+ 001 and LUMEX TV 007); the
// football game on VANTIX+ 004 has no other feed.
const GAME_FEED = 'ch:s_xt:9001'
const OTHER_FEED = 'ch:s_xt:9015'
const LONE_GAME = 'ch:s_xt:9004'

type Resolve = { id: string; replaces?: string | null }

/** Every resolve body the page sends, kept in sessionStorage (the mock worker hides request bodies from the test). */
async function recordResolves(page: Page) {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const req = new Request(input, init)
      if (req.method === 'POST' && new URL(req.url).pathname === '/api/v1/playback/resolve') {
        const seen = JSON.parse(sessionStorage.getItem('resolves') ?? '[]') as unknown[]
        sessionStorage.setItem('resolves', JSON.stringify([...seen, await req.clone().json()]))
      }
      return original(req)
    }
  })
}
const resolves = (page: Page): Promise<Resolve[]> => page.evaluate(() => JSON.parse(sessionStorage.getItem('resolves') ?? '[]'))

async function playUntilMoving(page: Page, id: string) {
  await page.goto(`/play/${encodeURIComponent(id)}`)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  await expect.poll(() => page.getByTestId('video').evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(1)
}

/** The picture stops moving while the player still means to play: what a feed that went quiet looks like. */
const freeze = (page: Page) => page.getByTestId('video').evaluate((v: HTMLVideoElement) => { v.playbackRate = 0 })

test('a game feed that stops switches to the next feed of the game and frees the old one', async ({ page }) => {
  await recordResolves(page)
  await signIn(page)
  await playUntilMoving(page, GAME_FEED)
  await freeze(page)
  await expect(page).toHaveURL(new RegExp(`/play/${encodeURIComponent(OTHER_FEED)}$`), { timeout: 15_000 })
  await expect(page.locator('.toast').last()).toHaveText(/stopped — switched to LUMEX TV 007$/)
  const bodies = await resolves(page)
  expect(bodies.at(-1)).toMatchObject({ id: OTHER_FEED, replaces: GAME_FEED })
  expect(bodies.filter((b) => b.replaces).length).toBe(1)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
})

test('a stalled channel with no other feed of its game stays on the player\'s own recovery', async ({ page }) => {
  await recordResolves(page)
  await signIn(page)
  await playUntilMoving(page, LONE_GAME)
  await freeze(page)
  await page.waitForTimeout(8_000)
  await expect(page).toHaveURL(new RegExp(`/play/${encodeURIComponent(LONE_GAME)}$`))
  const bodies = await resolves(page)
  expect(bodies.length).toBeGreaterThan(0)
  expect(bodies.every((b) => b.id === LONE_GAME && !b.replaces)).toBe(true)
})

test('a picture the viewer paused is never switched away from', async ({ page }) => {
  await signIn(page)
  await playUntilMoving(page, GAME_FEED)
  await page.getByTestId('video').evaluate((v: HTMLVideoElement) => v.pause())
  await page.waitForTimeout(8_000)
  await expect(page).toHaveURL(new RegExp(`/play/${encodeURIComponent(GAME_FEED)}$`))
})
