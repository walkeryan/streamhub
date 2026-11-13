import { expect, test, type Page } from '@playwright/test'
import { focusBarEntry, focusedLabel, searchBox, signIn } from './helpers'

const focusedKeyIn = (page: Page, testId: string) =>
  page.evaluate((id) => !!document.querySelector(`[data-testid="${id}"] .fx[data-focused="true"]`), testId)

const barFocus = (page: Page) => page.locator('[data-testid="player-bar"] .fx[data-focused="true"]')
/** Right along the control bar until the Audio button has focus: the buttons are icons, named by their labels. */
async function walkToAudio(page: Page) {
  for (let i = 0; i < 10 && (await barFocus(page).getAttribute('aria-label')) !== 'Audio'; i++) await page.keyboard.press('ArrowRight')
}

async function openLiveChannel(page: Page) {
  await signIn(page)
  await page.goto('/live')
  await expect(page.getByTestId('channel-row').first()).toBeVisible()
  await expect(page.getByTestId('channel-row').first()).toHaveAttribute('data-focused', 'true')
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
  await walkToAudio(page)
  await expect(barFocus(page)).toHaveAttribute('aria-label', 'Audio')
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
  await walkToAudio(page)
  await page.keyboard.press('Enter')
  await expect.poll(() => focusedKeyIn(page, 'player-menu')).toBe(true)
  await page.clock.install()
  await page.keyboard.press('Escape')
  await expect(barFocus(page)).toHaveAttribute('aria-label', 'Audio')
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
  await focusBarEntry(page, 'nav-search')
  await page.keyboard.press('Enter')
  await expect(searchBox(page)).toBeFocused()
  await page.keyboard.type('harbor')
  await expect(page).toHaveURL(/\/search\?q=harbor$/)
  await expect(page.getByTestId('search-titles')).toBeVisible({ timeout: 8000 })
  // Down from the box into the results, along to the poster, and open it
  await page.keyboard.press('ArrowDown')
  await expect(page.getByTestId('search-results').locator('.fx[data-focused="true"]')).toHaveCount(1)
  for (let i = 0; i < 6 && !(await page.getByTestId('search-titles').locator('.fx[data-focused="true"]').count()); i++) await page.keyboard.press('ArrowDown')
  const label = await focusedLabel(page)
  expect(label).toBe('Harbor Lights')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/title\//)
  await page.keyboard.press('Escape')
  await expect(page).toHaveURL(/\/search\?q=harbor$/)
  await expect(searchBox(page)).toHaveValue('harbor')
  await expect(page.getByTestId('search-titles')).toBeVisible()
  await expect.poll(() => focusedLabel(page)).toBe(label)
  // A query that led somewhere is offered again on the empty search page; the box itself starts empty.
  await page.goto('/')
  await expect(page.getByTestId('hero-play')).toHaveAttribute('data-focused', 'true')
  await expect(searchBox(page)).toHaveValue('')
  await focusBarEntry(page, 'nav-search')
  await page.keyboard.press('Enter')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('search-idle').getByRole('button', { name: 'harbor', exact: true })).toBeVisible()
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
  test('UAT P1-1: bar entries sit inside the 48/27 dp safe area; Up from the first row reaches the bar, Left from the first card does not', async ({ page }) => {
    await signIn(page)
    const dp = await page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).fontSize) / 16)
    const bar = (await page.getByTestId('rail').boundingBox())!
    const boxes = await page.locator('[data-testid="rail"] .rail-item').evaluateAll((els) => els.map((e) => e.getBoundingClientRect().toJSON() as DOMRect))
    expect(boxes.length).toBeGreaterThan(5)
    for (const b of boxes) {
      expect(b.x).toBeGreaterThanOrEqual(48 * dp - 0.5)
      expect(b.x + b.width).toBeLessThanOrEqual(1920 - 48 * dp + 0.5)
      expect(b.y).toBeGreaterThanOrEqual(27 * dp - 0.5)
      expect(b.y + b.height).toBeLessThanOrEqual(bar.height)
    }
    // The surface bleeds to the edges and stays a bar: about 68 dp of the screen's 720, not a panel.
    expect([bar.x, bar.y, bar.width]).toEqual([0, 0, 1920])
    expect(bar.height / dp).toBeGreaterThanOrEqual(64)
    expect(bar.height / dp).toBeLessThanOrEqual(80)
    // Down to the first card in the first row: Left stays on it, navigation is not to the left any more.
    const inBar = page.getByTestId('rail').locator('[data-focused="true"]')
    await page.keyboard.press('ArrowDown')
    const card = page.locator('[data-row="continue"] [data-testid="tile"]').first()
    await expect(card).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('ArrowLeft')
    await expect(card).toHaveAttribute('data-focused', 'true')
    await expect(inBar).toHaveCount(0)
    // Up through the hero to the bar, on the section being shown.
    await page.keyboard.press('ArrowUp')
    await expect(page.getByTestId('hero-play')).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('ArrowUp')
    await expect(page.getByTestId('nav-home')).toHaveAttribute('data-focused', 'true')
  })
})

test('Back during an uncommitted zap cancels pending playback instead of starting another stream', async ({ page }) => {
  await openLiveChannel(page)
  await page.evaluate(() => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }))
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
  })
  await expect(page).toHaveURL(/\/live/)
  await expect(page.getByTestId('channel-row').first()).toHaveAttribute('data-focused', 'true')
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
