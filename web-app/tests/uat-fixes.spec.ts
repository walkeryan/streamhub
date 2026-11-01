import { expect, test, type Page } from '@playwright/test'
import { focusedLabel, signIn } from './helpers'

const focusedKeyIn = (page: Page, testId: string) =>
  page.evaluate((id) => !!document.querySelector(`[data-testid="${id}"] .fx[data-focused="true"]`), testId)

async function openLiveChannel(page: Page) {
  await signIn(page)
  await page.goto('/live')
  await expect(page.getByTestId('channel-row').first()).toBeVisible()
  await expect(page.getByLabel('Search channels or number')).toBeFocused()
  await page.getByTestId('channel-row').first().click()
  await expect(page).toHaveURL(/\/play\/ch/)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing', { timeout: 15_000 })
}

test('UAT P1-2: live player control bar is reachable and usable with the D-pad', async ({ page }) => {
  await openLiveChannel(page)
  const ui = page.getByTestId('player-ui')
  const path = () => new URL(page.url()).pathname
  // controls start hidden on live; Down zaps while they are hidden
  await expect(ui).toHaveAttribute('data-show', 'false')
  const first = path()
  await page.keyboard.press('ArrowDown')
  await expect.poll(path).not.toBe(first)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  await expect(ui).toHaveAttribute('data-show', 'false')

  // Right reveals the controls, Down moves focus into the bar (Play/Pause first)
  await page.keyboard.press('ArrowRight')
  await expect(ui).toHaveAttribute('data-show', 'true')
  await page.keyboard.press('ArrowDown')
  await expect.poll(() => focusedKeyIn(page, 'player-bar')).toBe(true)
  await expect.poll(() => focusedLabel(page)).toMatch(/Pause|Play/)
  const before = path()
  // Left/Right move between buttons and do not zap; Enter activates the focused button
  for (let i = 0; i < 6 && !/Audio/.test((await page.locator('[data-testid="player-bar"] .fx[data-focused="true"]').textContent()) ?? ''); i++) {
    await page.keyboard.press('ArrowRight')
  }
  await expect(page.locator('[data-testid="player-bar"] .fx[data-focused="true"]')).toContainText('Audio and subtitles')
  expect(path()).toBe(before)
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('player-menu')).toBeVisible()
  await page.keyboard.press('Escape') // closes the menu, focus returns to the bar
  await expect(page.getByTestId('player-menu')).toBeHidden()
  await expect.poll(() => focusedKeyIn(page, 'player-bar')).toBe(true)

  // Up returns to the video; Up then zaps again
  await page.keyboard.press('ArrowUp')
  await expect.poll(() => focusedKeyIn(page, 'player-bar')).toBe(false)
  await page.keyboard.press('ArrowUp')
  await expect.poll(path).not.toBe(before)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')

  // OK with controls visible enters the bar; Back leaves the bar and hides the controls in one press
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('Enter')
  await expect.poll(() => focusedKeyIn(page, 'player-bar')).toBe(true)
  await page.keyboard.press('Escape')
  await expect(ui).toHaveAttribute('data-show', 'false')
  await expect(page).toHaveURL(/\/play\/ch/)
})

test('UAT P2-8: Back twice returns to the last channel; Back once leaves the player', async ({ page }) => {
  await openLiveChannel(page)
  const path = () => new URL(page.url()).pathname
  const first = path()
  await page.keyboard.press('ArrowUp')
  await expect.poll(path).not.toBe(first)
  const second = path()
  await expect(page.getByTestId('player-title')).toHaveText('Metro Sports 1')
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('back-hint')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect.poll(path).toBe(first)
  await expect(page.getByTestId('back-hint')).toBeHidden()
  expect(second).not.toBe(first)
  // Wait for the returned channel, not the previous render's playing state.
  await expect(page.getByTestId('player-title')).toHaveText('Harbor News 24')
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  // single Back: the hint shows, then the player closes and Live TV is back
  await page.keyboard.press('Escape')
  await expect(page).toHaveURL(/\/live/, { timeout: 3000 })
})

test('closing Audio restores focus immediately and cannot steal the next Up action', async ({ page }) => {
  await openLiveChannel(page)
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('ArrowDown')
  await expect.poll(() => focusedKeyIn(page, 'player-bar')).toBe(true)
  for (let i = 0; i < 6 && !/Audio/.test((await page.locator('[data-testid="player-bar"] .fx[data-focused="true"]').textContent()) ?? ''); i++) {
    await page.keyboard.press('ArrowRight')
  }
  await page.keyboard.press('Enter')
  await expect.poll(() => focusedKeyIn(page, 'player-menu')).toBe(true)
  await page.clock.install()
  await page.keyboard.press('Escape')
  await expect(page.locator('[data-testid="player-bar"] .fx[data-focused="true"]')).toContainText('Audio and subtitles')
  await page.keyboard.press('ArrowUp')
  await expect.poll(() => focusedKeyIn(page, 'player-bar')).toBe(false)
  await page.clock.fastForward(50)
  await expect.poll(() => focusedKeyIn(page, 'player-bar')).toBe(false)
  const before = new URL(page.url()).pathname
  await page.keyboard.press('ArrowUp')
  await expect.poll(() => new URL(page.url()).pathname).not.toBe(before)
})

test('UAT P1-4: Search keeps the query, results and focused result on Back', async ({ page }) => {
  await signIn(page)
  await page.keyboard.press('ArrowLeft')
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/search/)
  await expect(page.getByLabel('Search titles and channels')).toBeFocused()
  await page.keyboard.type('harbor')
  await expect(page.getByTestId('group-play')).toBeVisible({ timeout: 8000 })
  // Right from the field into the results, open the first result
  await page.keyboard.press('ArrowRight')
  const label = await focusedLabel(page)
  expect(label).toBeTruthy()
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/(title|play)\//)
  await page.keyboard.press('Escape')
  await expect(page).toHaveURL(/\/search/)
  await expect(page.getByLabel('Search titles and channels')).toHaveValue('harbor')
  await expect(page.getByTestId('group-play')).toBeVisible()
  await expect.poll(() => focusedLabel(page)).toBe(label)
  // Returning from the rail preserves this profile's latest search.
  await page.goto('/')
  await expect(page.getByTestId('home')).toBeVisible()
  await page.keyboard.press('ArrowLeft'); await page.keyboard.press('ArrowDown'); await page.keyboard.press('Enter')
  await expect(page.getByLabel('Search titles and channels')).toHaveValue('harbor')
})

test('UAT P1-7: failed artwork never shows a broken-image glyph', async ({ page }) => {
  // every image except the attribution logos fails to load
  await page.addInitScript(() => {
    const desc = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src')!
    Object.defineProperty(HTMLImageElement.prototype, 'src', {
      ...desc,
      set(v: string) { desc.set!.call(this, String(v).includes('/attribution/') ? v : '/__missing__/art.png') },
    })
  })
  await signIn(page)
  await expect(page.getByTestId('hero')).toBeVisible()
  const broken = () => page.evaluate(() => Array.from(document.images).filter((i) => i.complete && i.naturalWidth === 0 && i.getClientRects().length > 0).length)
  await expect.poll(broken).toBe(0)
  await expect(page.getByTestId('hero')).toHaveAttribute('data-art', 'none')
  await expect(page.getByTestId('img-fallback').first()).toBeVisible()
  await page.goto('/live')
  await expect(page.getByTestId('channel-row').first()).toBeVisible()
  await expect.poll(broken).toBe(0)
  await expect(page.getByTestId('img-fallback').first()).toBeVisible()
  await page.goto('/guide')
  await expect.poll(broken).toBe(0)
})

test('UAT P1-6: TMDB and JustWatch attribution logos render in About', async ({ page }) => {
  await signIn(page)
  await page.goto('/settings?tab=about')
  for (const alt of ['TMDB', 'JustWatch']) {
    const img = page.getByTestId('attribution').getByAltText(alt)
    await expect(img).toBeVisible()
    await expect.poll(() => img.evaluate((i: HTMLImageElement) => i.naturalWidth)).toBeGreaterThan(0)
  }
})

test.describe('TV width', () => {
  test.use({ viewport: { width: 1920, height: 1080 } })
  test('UAT P1-1: rail items sit inside the 48/27 dp safe area; Left from the first card opens the rail', async ({ page }) => {
    await signIn(page)
    const dp = await page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).fontSize) / 16)
    const boxes = await page.locator('[data-testid="rail"] .rail-item').evaluateAll((els) => els.map((e) => e.getBoundingClientRect().toJSON() as DOMRect))
    expect(boxes.length).toBeGreaterThan(5)
    for (const b of boxes) {
      expect(b.x).toBeGreaterThanOrEqual(48 * dp - 0.5)
      expect(b.y).toBeGreaterThanOrEqual(27 * dp - 0.5)
      expect(b.y + b.height).toBeLessThanOrEqual(1080 - 27 * dp + 0.5)
    }
    const railW = await page.getByTestId('rail').evaluate((e) => e.getBoundingClientRect().width)
    expect(railW / dp).toBeGreaterThanOrEqual(100)
    expect(railW / dp).toBeLessThanOrEqual(108)
    // Down to the first card in the first row, then Left opens the rail
    await page.keyboard.press('ArrowDown')
    await expect(page.getByTestId('rail')).toHaveAttribute('data-expanded', 'false')
    await page.keyboard.press('ArrowLeft')
    await expect(page.getByTestId('rail')).toHaveAttribute('data-expanded', 'true')
  })
})

test('Back during an uncommitted zap cancels pending playback instead of starting another stream', async ({ page }) => {
  await openLiveChannel(page)
  await page.evaluate(() => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }))
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
  })
  await expect(page).toHaveURL(/\/live/)
  await expect(page.getByLabel('Search channels or number')).toBeFocused()
})

test('approved live playback retains the exact750ms double-Back behavior', async ({ page }) => {
  await openLiveChannel(page)
  const first = new URL(page.url()).pathname
  await page.keyboard.press('ArrowUp')
  await expect.poll(() => new URL(page.url()).pathname).not.toBe(first)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  // Freeze at a known future instant: host/browser wall clocks can differ.
  await page.clock.install({ time: new Date("2026-11-01T12:00:00Z") })
  await page.clock.pauseAt(new Date("2026-11-01T12:01:00Z"))
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('back-hint')).toBeVisible()
  await page.clock.runFor(749)
  await expect(page).toHaveURL(/\/play\/ch/)
  await page.keyboard.press('Escape')
  await expect.poll(() => new URL(page.url()).pathname).toBe(first)
  await expect(page.getByTestId('back-hint')).toBeHidden()
  await page.clock.runFor(500)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('back-hint')).toBeVisible()
  await page.clock.runFor(749)
  await expect(page).toHaveURL(/\/play\/ch/)
  await page.clock.runFor(1)
  await expect(page).toHaveURL(/\/live/)
})
