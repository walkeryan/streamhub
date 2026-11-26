import { expect, test, type Page } from '@playwright/test'

// A stand-in controller, as in gamepad-nav.spec.ts: one standard-mapping pad whose buttons and sticks
// the test moves, the way an Xbox controller shows up in Edge. Signed in as Guest with the notice accepted.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const profile = { id: 'p2', name: 'Guest', avatar: null, is_admin: false, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' }
    sessionStorage.setItem('mock.accepted', JSON.stringify(['p1', 'p2', 'p3']))
    if (!localStorage.getItem('sh.session.v1')) localStorage.setItem('sh.session.v1', JSON.stringify({ access: 'mock-access-p2', refresh: 'mock-refresh-p2', profile }))
    const pressed = Array(17).fill(false)
    const axes = [0, 0, 0, 0]
    const pad = {
      id: 'Test controller', index: 0, connected: true, mapping: 'standard', timestamp: 0, axes,
      get buttons() { return pressed.map((p) => ({ pressed: p, touched: p, value: p ? 1 : 0 })) },
    }
    Object.defineProperty(navigator, 'getGamepads', { value: () => [pad, null, null, null] })
    Object.assign(window, { __pad: pressed, __axes: axes })
  })
})

type Pad = { __pad: boolean[]; __axes: number[] }
const B = 1, X = 2, Y = 3, LB = 4, RB = 5, LT = 6, RT = 7, START = 9, DOWN = 13

/** One tap of a standard-mapping button: held for a few frames, then let go. */
async function tap(page: Page, button: number) {
  await page.evaluate((b) => { (window as unknown as Pad).__pad[b] = true }, button)
  await page.waitForTimeout(80)
  await page.evaluate((b) => { (window as unknown as Pad).__pad[b] = false }, button)
  await page.waitForTimeout(80)
}

/** The right stick pushed up (-1) or down (1) for a moment. */
async function flick(page: Page, dir: -1 | 1, ms = 80) {
  await page.evaluate((d) => { (window as unknown as Pad).__axes[3] = d }, dir)
  await page.waitForTimeout(ms)
  await page.evaluate(() => { (window as unknown as Pad).__axes[3] = 0 })
  await page.waitForTimeout(80)
}

async function home(page: Page) {
  await page.goto('/')
  await expect(page.getByTestId('home')).toBeVisible()
}

async function play(page: Page, id: string) {
  await page.evaluate((path) => { history.pushState({ idx: 1 }, '', path); window.dispatchEvent(new PopStateEvent('popstate')) }, `/play/${encodeURIComponent(id)}`)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing', { timeout: 20_000 })
}

// A film only the provider has: a restart-at-offset stream, 1 h 52 min long, that can be moved in.
const FILM = 'tmdb:movie:108'
const CHANNEL = 'ch:s_xt:1001'
const paused = (page: Page) => page.locator('video').evaluate((v: HTMLVideoElement) => v.paused)
const path = (page: Page) => new URL(page.url()).pathname
const noSideScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)

test('the first controller press shows a short hint once per browser', async ({ page }) => {
  await home(page)
  await expect(page.getByTestId('controller-hint')).toHaveCount(0)
  await tap(page, DOWN)
  const hint = page.getByTestId('controller-hint')
  await expect(hint).toBeVisible()
  await expect(hint).toContainText('Controller connected')
  for (const button of ['A', 'B', 'X', 'LB', 'RB', 'Start']) await expect(hint.getByRole('img', { name: button, exact: true })).toBeVisible()
  // It takes no focus: the controller goes on moving it beneath the hint.
  expect(await page.evaluate(() => !!document.activeElement?.closest('[data-testid="controller-hint"]'))).toBe(false)
  await hint.getByRole('button', { name: 'Close' }).click()
  await expect(hint).toHaveCount(0)
  // Once is enough: not again on this page, nor after a reload.
  await tap(page, DOWN)
  await page.reload()
  await expect(page.getByTestId('home')).toBeVisible()
  await tap(page, DOWN)
  await page.waitForTimeout(300)
  await expect(page.getByTestId('controller-hint')).toHaveCount(0)
})

test('a film: X plays and pauses, the bumpers and triggers step, Y and Start open information and the menu, the right stick is volume', async ({ page }) => {
  await home(page)
  await play(page, FILM)
  expect(await paused(page)).toBe(false)
  await tap(page, X)
  await expect.poll(() => paused(page)).toBe(true)
  // Paused, the controls stay up, with what the buttons do here.
  const hints = page.getByTestId('pad-hints')
  await expect(hints).toBeVisible()
  for (const text of ['Play', '10 s', '30 s', 'Volume', 'Info', 'Menu']) await expect(hints).toContainText(text)
  await expect(hints).not.toContainText('Channel')
  await tap(page, X)
  await expect.poll(() => paused(page)).toBe(false)

  // RB and RT add up to one pending seek, as the arrows do; LB and LT come back off it.
  const seekHint = page.getByTestId('seek-hint')
  await tap(page, RB)
  await expect(seekHint).toHaveText('+10 s')
  await tap(page, RT)
  await expect(seekHint).toHaveText('+40 s')
  await tap(page, LT)
  await expect(seekHint).toHaveText('+10 s')
  await tap(page, LB)
  await expect(seekHint).toHaveText(/^[+−]0 s$/)
  // B takes the seek back before anything else.
  await tap(page, B)
  await expect(seekHint).toHaveCount(0)

  await tap(page, Y)
  await expect(page.getByTestId('player-info')).toBeVisible()
  await tap(page, Y)
  await expect(page.getByTestId('player-info')).toHaveCount(0)

  await tap(page, START)
  const menu = page.getByTestId('player-menu')
  await expect(menu).toBeVisible()
  await expect(menu).toHaveAttribute('data-section', 'all')
  await tap(page, B)
  await expect(menu).toHaveCount(0)

  await flick(page, 1)
  await expect(page.getByTestId('player-flash')).toHaveText('Volume 95%')
  await flick(page, -1)
  await expect(page.getByTestId('player-flash')).toHaveText('Volume 100%')
  // A held stick turns on, like a held key.
  await flick(page, 1, 700)
  await expect.poll(() => page.locator('video').evaluate((v: HTMLVideoElement) => v.volume)).toBeLessThan(0.86)
})

test('a live channel: RB and LB change channel, the triggers do nothing, the banner shows the buttons', async ({ page }) => {
  await home(page)
  await play(page, CHANNEL)
  const first = path(page)
  // Controls hidden: the channel banner carries the controller's hints instead of the remote's.
  await tap(page, RB)
  await expect.poll(() => path(page)).not.toBe(first)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing', { timeout: 20_000 })
  const banner = page.getByTestId('osd')
  await expect(banner.getByTestId('pad-hints')).toContainText('Channel')
  await expect(banner).not.toContainText('change channel · ◀ ▶ show controls')
  await tap(page, LB)
  await expect.poll(() => path(page)).toBe(first)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing', { timeout: 20_000 })
  await tap(page, RT)
  await tap(page, LT)
  await page.waitForTimeout(300)
  expect(path(page)).toBe(first)
  await expect(page.getByTestId('seek-hint')).toHaveCount(0)
  // With the controls up the row is under the bar, and it offers no seeking.
  await tap(page, X)
  const hints = page.getByTestId('player-ui').getByTestId('pad-hints')
  await expect(hints).toBeVisible()
  await expect(hints).toContainText('Channel')
  await expect(hints).not.toContainText('30 s')
})

test('the shortcut sheet lists the controller; the hint row and the sheet fit a TV and a phone', async ({ page }) => {
  await home(page)
  await play(page, FILM)
  await tap(page, START)
  await page.getByTestId('player-keys-btn').click()
  const sheet = page.getByTestId('player-shortcuts')
  await expect(sheet).toBeVisible()
  await expect(sheet).toContainText('PgUp')
  await sheet.getByRole('tab', { name: 'Controller' }).click()
  const pad = sheet.getByTestId('controller-help')
  await expect(pad).toContainText('Controller')
  for (const text of ['Select', 'Play or pause', 'Stream information', 'Menu: audio, subtitles, quality and more', 'Back or forward 10 seconds', 'Back or forward 30 seconds', 'volume']) await expect(pad).toContainText(text)
  await expect(pad).not.toContainText('Previous or next channel')
  for (const size of [{ width: 1920, height: 1080 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(size)
    expect(await noSideScroll(page)).toBe(true)
    const box = (await pad.boundingBox())!
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(size.width)
  }
  await tap(page, B)
  await expect(sheet).toHaveCount(0)
  await page.setViewportSize({ width: 1280, height: 720 })

  // The row under the bar wraps rather than run off a phone.
  await tap(page, X)
  const hints = page.getByTestId('pad-hints')
  for (const size of [{ width: 1920, height: 1080 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(size)
    await expect(hints).toBeVisible()
    expect(await noSideScroll(page)).toBe(true)
    const box = (await hints.boundingBox())!
    expect(box.x + box.width).toBeLessThanOrEqual(size.width)
  }
})

test('away from the player the right stick scrolls the page, and the player keys do nothing', async ({ page }) => {
  await home(page)
  const scrolled = () => page.evaluate(() => {
    let el: Element | null = document.activeElement
    for (; el && el !== document.body; el = el.parentElement) if (el.scrollTop > 0) return el.scrollTop
    return window.scrollY
  })
  expect(await scrolled()).toBe(0)
  const url = page.url()
  for (const button of [X, Y, START, LB, RB, LT, RT]) await tap(page, button)
  await page.waitForTimeout(200)
  expect(page.url()).toBe(url)
  await expect(page.getByTestId('home')).toBeVisible()
  await flick(page, 1, 600)
  await expect.poll(scrolled).toBeGreaterThan(0)
})
