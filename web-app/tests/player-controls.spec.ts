import { expect, test, type Page } from '@playwright/test'

// A library film: the mock serves it as the file it is (30 s, not seekable through the mock service worker).
const FILM = 'tmdb:movie:110'
// A film only the provider has: a restart-at-offset stream, 1 h 52 min long, that holds 30 s from wherever it started.
const CONVERTED = 'tmdb:movie:108'
const LENGTH_S = 112 * 60

type Resolve = { id: string; start_position_ms?: number | null; max_height?: number | null }
type Watched = { __seeks: number[]; __session: Record<string, ((details: { seekTime?: number }) => void) | null>; __noteResolve: (body: Resolve) => void }

/** Signed in as Guest with the first-run notice accepted, and every resolve the player makes on record. */
async function enter(page: Page): Promise<Resolve[]> {
  const resolves: Resolve[] = []
  await page.exposeFunction('__noteResolve', (body: Resolve) => { resolves.push(body) })
  await page.addInitScript(() => {
    const profile = { id: 'p2', name: 'Guest', avatar: null, is_admin: false, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' }
    sessionStorage.setItem('mock.accepted', JSON.stringify(['p1', 'p2', 'p3']))
    if (!localStorage.getItem('sh.session.v1')) localStorage.setItem('sh.session.v1', JSON.stringify({ access: 'mock-access-p2', refresh: 'mock-refresh-p2', profile }))
    // Every position the element is moved to, and the Media Session handlers the player registers.
    const w = window as unknown as Watched
    w.__seeks = []
    const time = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime')!
    Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', { ...time, set(value: number) { w.__seeks.push(value); time.set!.call(this, value) } })
    w.__session = {}
    const set = navigator.mediaSession.setActionHandler.bind(navigator.mediaSession)
    navigator.mediaSession.setActionHandler = (action, handler) => { w.__session[action] = handler as Watched['__session'][string]; set(action, handler) }
    // A resolve can be held back, to look at the bar while a restart is on its way.
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const request = new Request(input, init)
      if (new URL(request.url).pathname.endsWith('/playback/resolve')) {
        w.__noteResolve(await request.clone().json() as Resolve)
        const wait = Number(sessionStorage.getItem('test.resolveDelay') ?? 0)
        if (wait) await new Promise((done) => setTimeout(done, wait))
      }
      return original(request)
    }
  })
  await page.goto('/')
  await expect(page.getByTestId('home')).toBeVisible()
  return resolves
}

async function play(page: Page, id: string) {
  await page.evaluate((path) => { history.pushState({ idx: 1 }, '', path); window.dispatchEvent(new PopStateEvent('popstate')) }, `/play/${encodeURIComponent(id)}`)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing', { timeout: 20_000 })
}

const ui = (page: Page) => page.getByTestId('player-ui')
const track = (page: Page) => page.getByTestId('scrub-track')
const video = <T>(page: Page, read: (v: HTMLVideoElement) => T) => page.locator('video').evaluate(read)
const seeks = (page: Page) => page.evaluate(() => (window as unknown as Watched).__seeks.slice())
const inBar = (page: Page) => page.evaluate(() => !!document.querySelector('.player-ui .bottom .fx[data-focused="true"]'))
const focusLabel = (page: Page) => page.locator('.player-ui .bottom .fx[data-focused="true"]').getAttribute('aria-label')
const startsAt = (page: Page) => page.getByTestId('player').getAttribute('data-stream-start').then(Number)
/** A point on the scrubber, as a fraction of its width. */
async function on(page: Page, fraction: number) {
  const box = (await track(page).boundingBox())!
  return { x: box.x + box.width * fraction, y: box.y + box.height / 2 }
}

test('the control bar is one row of named controls; the time readout can count down', async ({ page }) => {
  await enter(page)
  await play(page, CONVERTED)
  await page.mouse.move(600, 300)
  const bar = page.getByTestId('player-bar')
  for (const name of ['Pause', 'Back 10 seconds', 'Forward 10 seconds', 'Mute', 'Subtitles', 'Audio', 'Settings', 'Stream information', 'Picture in picture', 'Full screen']) {
    await expect(bar.getByRole('button', { name, exact: true })).toBeVisible()
  }
  for (const size of [{ width: 1280, height: 720 }, { width: 1920, height: 1080 }]) {
    await page.setViewportSize(size)
    const tops = await bar.locator('.pbar-row .fx').evaluateAll((all) => all.map((el) => Math.round(el.getBoundingClientRect().top + el.getBoundingClientRect().height / 2)))
    expect(Math.max(...tops) - Math.min(...tops)).toBeLessThanOrEqual(2)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(size.width)
  }
  // Position and length, or on a click what is left; the choice is this browser's.
  const time = page.getByTestId('player-time')
  await expect(time).toHaveText(/^0:0\d \/ 1:52:00$/)
  await time.click()
  await expect(time).toHaveText(/^0:\d\d \/ −1:51:\d\d$/)
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('sh.player.v1')!).remaining)).toBe(true)
  // The volume slider comes out for a mouse, and muting is one click.
  await page.getByTestId('player-mute').hover()
  const slider = page.getByTestId('player-volume')
  await expect.poll(async () => (await slider.boundingBox())?.width ?? 0).toBeGreaterThan(40)
  await slider.fill('30')
  expect(await video(page, (v) => v.volume)).toBeCloseTo(0.3)
  await page.getByTestId('player-mute').click()
  await expect(page.getByTestId('player-mute')).toHaveAttribute('aria-label', 'Unmute')
  expect(await video(page, (v) => v.muted)).toBe(true)
})

test('the scrubber shows the time under the pointer, previews a drag and seeks once on release; a click jumps', async ({ page }) => {
  const resolves = await enter(page)
  await play(page, CONVERTED)
  expect(resolves).toHaveLength(1)
  // The scale is the whole title, not the half minute the stream holds.
  const half = await on(page, 0.5)
  await page.mouse.move(half.x, half.y)
  await expect(page.getByTestId('scrub-bubble')).toHaveText(/^5[56]:\d\d$/)
  // What the stream holds is drawn apart from what has been played.
  const loaded = await page.getByTestId('scrub-loaded').first().evaluate((el) => el.getBoundingClientRect().width)
  const played = await page.getByTestId('scrub-played').evaluate((el) => el.getBoundingClientRect().width)
  expect(loaded).toBeGreaterThan(played)

  // Press, drag back to a quarter: a second thumb and the bubble show the target, and nothing has been asked for yet.
  const before = await seeks(page)
  await page.mouse.down()
  const quarter = await on(page, 0.25)
  await page.mouse.move(quarter.x, quarter.y, { steps: 8 })
  await expect(page.getByTestId('scrub-ghost')).toBeVisible()
  await expect(page.getByTestId('scrub-bubble')).toHaveText(/^2[78]:\d\d$/)
  await expect(track(page)).toHaveAttribute('data-dragging', 'true')
  await page.waitForTimeout(700)
  expect(resolves).toHaveLength(1)
  expect(await seeks(page)).toEqual(before)
  // Release: one seek, which for a place the stream does not hold is one new stream.
  await page.mouse.up()
  await expect.poll(() => resolves.length).toBe(2)
  expect(Math.abs(resolves[1]!.start_position_ms! / 1000 - LENGTH_S / 4)).toBeLessThan(15)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  expect(Math.abs((await startsAt(page)) / 1000 - LENGTH_S / 4)).toBeLessThan(15)
  await expect(page.getByTestId('scrub-ghost')).toHaveCount(0)

  // A click is a press and a release in one place.
  const late = await on(page, 0.75)
  await page.mouse.click(late.x, late.y)
  await expect.poll(() => resolves.length).toBe(3)
  expect(Math.abs(resolves[2]!.start_position_ms! / 1000 - LENGTH_S * 0.75)).toBeLessThan(15)
  // The click did not reach the play button behind the bar.
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
})

test('key presses add up to one seek: shown at once, made when they pause; Enter makes it now and Escape drops it', async ({ page }) => {
  const resolves = await enter(page)
  await play(page, CONVERTED)
  // Three minutes on, as three presses. The bar and the hint follow each press; the server hears nothing.
  for (let i = 0; i < 3; i++) await page.keyboard.press('Shift+ArrowRight')
  await expect(page.getByTestId('seek-hint')).toHaveText('+3 min')
  await expect(page.getByTestId('scrub-bubble')).toContainText('+3 min')
  expect(Number(await track(page).getAttribute('data-target'))).toBeGreaterThanOrEqual(180)
  expect(resolves).toHaveLength(1)
  // The presses pause: one new stream, at the target (on the server's five-second grid).
  await expect.poll(() => resolves.length).toBe(2)
  expect(resolves[1]!.start_position_ms).toBeGreaterThanOrEqual(180_000)
  expect(resolves[1]!.start_position_ms).toBeLessThan(190_000)
  await expect(page.getByTestId('seek-hint')).toHaveCount(0)
  await expect(page.getByTestId('player')).toHaveAttribute('data-stream-start', '180000')
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')

  // Escape takes a pending target away and nothing else: no seek, the player stays, the controls stay.
  await page.keyboard.press('Shift+ArrowLeft')
  await page.keyboard.press('Shift+ArrowLeft')
  await expect(page.getByTestId('seek-hint')).toHaveText('−2 min')
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('seek-hint')).toHaveCount(0)
  await page.waitForTimeout(900)
  expect(resolves).toHaveLength(2)
  await expect(page.getByTestId('player')).toBeVisible()

  // Inside what the stream holds a step costs nothing: the picture moves and no stream is started.
  const local = await video(page, (v) => v.currentTime)
  await page.keyboard.press('l')
  await expect(page.getByTestId('seek-hint')).toHaveText('+10 s')
  await expect.poll(async () => (await seeks(page)).at(-1) ?? 0).toBeGreaterThanOrEqual(local + 9)
  await expect(page.getByTestId('seek-hint')).toHaveCount(0)
  expect(resolves).toHaveLength(2)

  // A number is a tenth of the title; a second number replaces the first and both cost one stream. Enter does not wait.
  await page.keyboard.press('3')
  await page.keyboard.press('5')
  expect(Number(await track(page).getAttribute('data-target'))).toBe(LENGTH_S / 2)
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('seek-hint')).toHaveCount(0)
  await expect.poll(() => resolves.length).toBe(3)
  expect(resolves[2]!.start_position_ms).toBe(LENGTH_S * 500)
  await expect(page.getByTestId('player')).toHaveAttribute('data-stream-start', String(LENGTH_S * 500))
  await page.waitForTimeout(900)
  expect(resolves).toHaveLength(3)
})

test('while a restart is on its way the bar already shows the target, with a spinner', async ({ page }) => {
  const resolves = await enter(page)
  await play(page, CONVERTED)
  await page.evaluate(() => sessionStorage.setItem('test.resolveDelay', '2500'))
  await page.keyboard.press('End')
  await page.keyboard.press('Enter')
  // Ten seconds before the end: the thumb is there now, the old position is not shown, and the controls do not hide.
  await expect(track(page)).toHaveAttribute('data-busy', 'true')
  await expect(track(page)).toHaveAttribute('data-position', String(LENGTH_S - 10))
  await expect(page.getByTestId('scrub-thumb')).toHaveAttribute('data-busy', 'true')
  await expect(page.getByTestId('player-time')).toHaveText(/^1:51:50 \//)
  await expect(ui(page)).toHaveAttribute('data-show', 'true')
  // More presses while it is on its way carry on from the target, and replace it.
  await page.keyboard.press('Shift+ArrowLeft')
  await expect(page.getByTestId('seek-hint')).toHaveText('−1 min')
  await page.evaluate(() => sessionStorage.removeItem('test.resolveDelay'))
  await expect(page.getByTestId('player')).toHaveAttribute('data-stream-start', String((LENGTH_S - 70) * 1000))
  await expect(track(page)).toHaveAttribute('data-busy', 'false')
  expect(resolves.at(-1)!.start_position_ms).toBe((LENGTH_S - 70) * 1000)
})

test('shortcuts: mute, volume, speed, menus, help; volume, mute and speed are remembered by the browser', async ({ page }) => {
  await enter(page)
  await play(page, CONVERTED)
  const flash = page.getByTestId('player-flash')
  await page.keyboard.press('m')
  await expect(flash).toHaveText('Muted')
  await expect.poll(() => video(page, (v) => v.muted)).toBe(true)
  await page.keyboard.press('m')
  await expect(flash).toHaveText('Volume 100%')
  await expect.poll(() => video(page, (v) => v.muted)).toBe(false)
  // Up and Down are the volume while focus is on the picture, and do not move it into the bar.
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('ArrowDown')
  await expect(flash).toHaveText('Volume 90%')
  expect(await video(page, (v) => v.volume)).toBeCloseTo(0.9)
  expect(await inBar(page)).toBe(false)
  await page.keyboard.press('ArrowUp')
  await expect(flash).toHaveText('Volume 95%')
  // Faster and slower, a quarter at a time.
  await page.keyboard.press('Shift+>')
  await expect(flash).toHaveText('Speed 1.25×')
  expect(await video(page, (v) => v.playbackRate)).toBe(1.25)
  // Settings: the speed in force is the pressed one; choosing another applies it.
  await page.keyboard.press('s')
  const menu = page.getByTestId('player-menu')
  await expect(menu).toHaveAttribute('data-section', 'settings')
  await expect(menu.getByTestId('player-speed')).toHaveText(['0.5×', '0.75×', 'Normal', '1.25×', '1.5×', '1.75×', '2×'])
  await expect(menu.getByRole('button', { name: '1.25×', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await menu.getByRole('button', { name: '1.5×', exact: true }).click()
  expect(await video(page, (v) => v.playbackRate)).toBe(1.5)
  // What the menu offered before is still there.
  await expect(menu.getByRole('button', { name: 'Stretch', exact: true })).toBeVisible()
  await expect(menu.getByRole('button', { name: '30 min', exact: true })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(menu).toHaveCount(0)
  // Audio and subtitles have a menu each; C switches subtitles and says so when there are none.
  await page.keyboard.press('a')
  await expect(menu).toHaveAttribute('data-section', 'audio')
  await page.keyboard.press('Escape')
  await page.keyboard.press('c')
  await expect(flash).toHaveText('No subtitles in this stream')
  // The sheet lists the keys; Escape closes it and the player is still there.
  await page.keyboard.press('Shift+?')
  const sheet = page.getByTestId('player-shortcuts')
  await expect(sheet).toContainText('Play or pause')
  await expect(sheet).toContainText('Jump to 0% … 90% of the title')
  // A dialog has the keys: a letter does nothing behind it.
  await page.keyboard.press('m')
  expect(await video(page, (v) => v.muted)).toBe(false)
  await page.keyboard.press('Escape')
  await expect(sheet).toHaveCount(0)
  await expect(page.getByTestId('player')).toBeVisible()
  // A modifier leaves the key to the browser.
  await page.keyboard.press('Control+m')
  expect(await video(page, (v) => v.muted)).toBe(false)

  // The next title in this browser starts as this one was left.
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('sh.player.v1')!))).toMatchObject({ volume: 0.95, muted: false, speed: 1.5 })
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  await page.keyboard.press('m')
  await page.reload()
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing', { timeout: 20_000 })
  await expect.poll(() => video(page, (v) => [v.muted, v.playbackRate, Math.round(v.volume * 100)])).toEqual([true, 1.5, 95])
  await expect(page.getByTestId('player-mute')).toHaveAttribute('aria-label', 'Unmute')
})

test('a remote: OK pauses onto the scrubber, the arrows reach every control, and the controls hide only when nothing holds them', async ({ page }) => {
  const resolves = await enter(page)
  await play(page, CONVERTED)
  // Playing and left alone, the controls go after about three seconds.
  await expect(ui(page)).toHaveAttribute('data-show', 'true')
  await expect(ui(page)).toHaveAttribute('data-show', 'false', { timeout: 6000 })
  // OK pauses and stands on the scrubber. Paused, the controls stay.
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'paused')
  await expect.poll(() => focusLabel(page)).toMatch(/^Seek\. /)
  await page.waitForTimeout(3600)
  await expect(ui(page)).toHaveAttribute('data-show', 'true')
  // Left and Right on the scrubber look for a place; OK goes there without waiting.
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('ArrowRight')
  await expect(page.getByTestId('seek-hint')).toHaveText('+40 s')
  await page.keyboard.press('Enter')
  await expect.poll(() => resolves.length).toBe(2)
  await expect(page.getByTestId('player')).toHaveAttribute('data-stream-start', '40000')
  await expect(page.getByTestId('seek-hint')).toHaveCount(0)
  // The film was paused when it was moved, so the new stream waits paused too.
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'paused')
  await expect.poll(() => video(page, (v) => v.paused && v.readyState >= 2)).toBe(true)
  // Down to the buttons, along them, into a menu and back; Up to the scrubber, Up again to the picture.
  await page.keyboard.press('ArrowDown')
  await expect.poll(() => focusLabel(page)).toMatch(/^(Play|Pause)$/)
  const seen: string[] = []
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press('ArrowRight')
    const label = (await focusLabel(page)) ?? ''
    if (seen.at(-1) === label) break
    seen.push(label)
  }
  expect(seen.map((label) => label.replace(/^\d.*/, 'time'))).toEqual(['Back 10 seconds', 'Forward 10 seconds', 'Mute', 'time', 'Subtitles', 'Audio', 'Settings', 'Stream information', 'Picture in picture', 'Full screen'])
  for (let i = 0; i < 6 && (await focusLabel(page)) !== 'Audio'; i++) await page.keyboard.press('ArrowLeft')
  await expect.poll(() => focusLabel(page)).toBe('Audio')
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('player-menu')).toHaveAttribute('data-section', 'settings')
  await expect(page.getByTestId('player-menu').locator('.fx[data-focused="true"]')).toHaveText('0.5×')
  await page.keyboard.press('Escape')
  await expect.poll(() => focusLabel(page)).toBe('Settings')
  await page.keyboard.press('ArrowUp')
  await expect.poll(() => focusLabel(page)).toMatch(/^Seek\. /)
  await page.keyboard.press('ArrowUp')
  await expect.poll(() => inBar(page)).toBe(false)
  // OK on the picture plays again; with focus out of the bar and nothing pending the controls hide.
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  await expect(ui(page)).toHaveAttribute('data-show', 'false', { timeout: 6000 })
  // Pausing from the picture and resuming with OK on Play does not leave the remote stuck in a bar that cannot hide.
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'paused')
  // Down returns to the button that was used last; Play is a few to the left.
  await page.keyboard.press('ArrowDown')
  await expect.poll(() => focusLabel(page)).toBe('Settings')
  for (let i = 0; i < 10 && (await focusLabel(page)) !== 'Play'; i++) await page.keyboard.press('ArrowLeft')
  await expect.poll(() => focusLabel(page)).toBe('Play')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  await expect.poll(() => inBar(page)).toBe(false)
  await expect(ui(page)).toHaveAttribute('data-show', 'false', { timeout: 6000 })
  // Back: with the controls hidden it leaves.
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('player')).toHaveCount(0)
})

test('Media Session: title for the lock screen, and its play, pause and seek controls drive the player', async ({ page }) => {
  const resolves = await enter(page)
  await play(page, CONVERTED)
  await expect.poll(() => page.evaluate(() => navigator.mediaSession.metadata?.title)).toBe('The Understudy')
  const act = (action: string, details: { seekTime?: number } = {}) => page.evaluate(([name, d]) => (window as unknown as Watched).__session[name]!(d), [action, details] as const)
  expect(await page.evaluate(() => Object.entries((window as unknown as Watched).__session).filter(([, handler]) => handler).map(([name]) => name).sort())).toEqual(['pause', 'play', 'seekbackward', 'seekforward', 'seekto'])
  await act('pause')
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'paused')
  await act('play')
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  // A headset's skip buttons add up like the keys do.
  await act('seekforward')
  await act('seekforward')
  await expect(page.getByTestId('seek-hint')).toHaveText('+20 s')
  await expect(page.getByTestId('seek-hint')).toHaveCount(0)
  expect(resolves).toHaveLength(1)
  await act('seekto', { seekTime: 600 })
  await expect.poll(() => resolves.length).toBe(2)
  expect(resolves[1]!.start_position_ms).toBe(600_000)
  // Media keys on a keyboard or a remote.
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  await page.evaluate(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'MediaPlayPause', bubbles: true })))
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'paused')
  await page.evaluate(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'MediaFastForward', bubbles: true })))
  await expect(page.getByTestId('seek-hint')).toHaveText('+10 s')
  await page.evaluate(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'MediaStop', bubbles: true })))
  await expect(page.getByTestId('player')).toHaveCount(0)
})

test('stream information: what is playing, how it is delivered, and what the player measures', async ({ page }) => {
  await enter(page)
  await play(page, CONVERTED)
  await page.keyboard.press('Shift+ArrowRight')
  await expect(page.getByTestId('player')).toHaveAttribute('data-stream-start', '60000')
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  await page.keyboard.press('i')
  const panel = page.getByTestId('player-info')
  await expect(panel).toBeVisible()
  const rows = (section: string) => panel.locator(`[data-section="${section}"] dl > div`).evaluateAll((all) => all.map((row) => [row.querySelector('dt')!.textContent, row.querySelector('dd')!.textContent]))
  expect(await rows('Source')).toEqual([['From', 'IPTV'], ['Container', 'MKV'], ['Bit rate', '18 Mbit/s']])
  expect(await rows('Video')).toEqual([['Codec', 'HEVC (H.265)'], ['Resolution', '3840 × 1600'], ['Frame rate', '23.976 fps'], ['Range', 'HDR10']])
  expect(await rows('Audio')).toEqual([['Codec', 'E-AC-3'], ['Channels', '5.1'], ['Language', 'English']])
  expect(await rows('Delivery')).toEqual([['Method', 'Transcode: video re-encoded by hardware'], ['Audio sent as', 'AAC stereo'], ['Picture sent', '1920 × 800']])
  // The player's own numbers: where it is, where this stream began, what it has ahead, what it dropped.
  await expect.poll(async () => Object.fromEntries(await rows('Player'))).toMatchObject({ Position: expect.stringMatching(/^1:0\d$/), 'Stream starts at': '1:00', 'Buffered ahead': expect.stringMatching(/^\d+\.\d s$/), 'Dropped frames': expect.stringMatching(/^\d+ of \d+$/) })
  const first = Object.fromEntries(await rows('Player')).Position
  await expect.poll(async () => Object.fromEntries(await rows('Player')).Position).not.toBe(first)
  // It is a panel, not a dialog: playback and the keys carry on behind it, and the button says it is open.
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  await page.mouse.move(600, 300)
  await expect(page.getByTestId('player-info-btn')).toHaveAttribute('aria-pressed', 'true')
  await page.keyboard.press('k')
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'paused')
  // Back closes it before anything else; the player stays.
  await page.keyboard.press('Escape')
  await expect(panel).toHaveCount(0)
  await expect(page.getByTestId('player')).toBeVisible()
  await page.getByTestId('player-info-btn').click()
  await expect(panel).toBeVisible()
  await panel.getByRole('button', { name: 'Close stream information' }).click()
  await expect(panel).toHaveCount(0)
  // Nothing the server sends for it is an address.
  expect(JSON.stringify(await page.evaluate(() => document.querySelector('[data-testid="player"]')!.textContent))).not.toMatch(/https?:|\/mock\//)
})

test('quality: the choices below the source with what an hour costs; a change restarts where the film is and is remembered', async ({ page }) => {
  const resolves = await enter(page)
  await play(page, CONVERTED)
  expect(resolves[0]!.max_height ?? null).toBeNull()
  await page.keyboard.press('Shift+ArrowRight')
  await page.keyboard.press('Shift+ArrowRight')
  await expect(page.getByTestId('player')).toHaveAttribute('data-stream-start', '120000')
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  await page.keyboard.press('s')
  const menu = page.getByTestId('player-menu')
  const choices = menu.getByTestId('player-quality')
  // The file is 3840 x 1600: every cap is below it. Uncapped, the server transcodes it to at most 8 Mbit/s.
  await expect(choices).toHaveText(['Original (1600p)about 3.7 GB per hour', '1080pabout 2.3 GB per hour', '720pabout 1.3 GB per hour', '480pabout 700 MB per hour'])
  await expect(choices.first()).toHaveAttribute('aria-pressed', 'true')
  // The speed is still there beside it.
  await expect(menu.getByTestId('player-speed')).toHaveCount(7)
  const before = resolves.length
  await choices.filter({ hasText: '720p' }).click()
  await expect(menu).toHaveCount(0)
  await expect(page.getByTestId('player-flash')).toHaveText('Quality: 720p')
  // One new stream, capped, from where the film was: the bar shows that place while it comes.
  await expect.poll(() => resolves.length).toBe(before + 1)
  expect(resolves.at(-1)).toMatchObject({ max_height: 720 })
  expect(resolves.at(-1)!.start_position_ms).toBeGreaterThanOrEqual(120_000)
  expect(resolves.at(-1)!.start_position_ms).toBeLessThan(130_000)
  await expect(page.getByTestId('player')).toHaveAttribute('data-stream-start', '120000')
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  await expect(page.getByTestId('player-time')).toHaveText(/^2:0\d \//)
  // The information panel says what is now sent.
  await page.keyboard.press('i')
  const delivery = page.getByTestId('player-info').locator('[data-section="Delivery"]')
  await expect(delivery).toContainText('1728 × 720')
  await expect(delivery).toContainText('Limited to 720p')
  await page.keyboard.press('i')
  await page.keyboard.press('s')
  await expect(choices.filter({ hasText: '720p' })).toHaveAttribute('aria-pressed', 'true')
  // Choosing what is already in force starts nothing.
  await choices.filter({ hasText: '720p' }).click()
  await page.waitForTimeout(600)
  expect(resolves).toHaveLength(before + 1)
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('sh.player.quality.v1')!))).toEqual({ normal: 720, saver: null })

  // The browser remembers: a 1080p film it would have played as the file it is now arrives capped...
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('player')).toHaveCount(0)
  await play(page, FILM)
  expect(resolves.at(-1)).toMatchObject({ id: FILM, max_height: 720 })
  await expect(page.getByTestId('player')).toHaveAttribute('data-stream-start', '0')
  await page.keyboard.press('s')
  // ...and is offered only what is below its own height.
  await expect(choices).toHaveText(['Original (1080p)about 4.2 GB per hour', '720pabout 1.3 GB per hour', '480pabout 700 MB per hour'])
  await expect(choices.filter({ hasText: '720p' })).toHaveAttribute('aria-pressed', 'true')
  // Back to Original: the file itself again, carried on from where the capped stream was.
  await expect.poll(() => video(page, (v) => v.currentTime)).toBeGreaterThan(1.5)
  const sought = (await seeks(page)).length
  await choices.first().click()
  await expect.poll(() => resolves.at(-1)!.max_height ?? null).toBeNull()
  expect(resolves.at(-1)!.start_position_ms).toBeGreaterThan(1000)
  await expect(page.getByTestId('player')).not.toHaveAttribute('data-stream-start')
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  const carried = (await seeks(page)).slice(sought)
  expect(carried.some((at) => at > 1 && at < 15)).toBe(true)
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('sh.player.quality.v1')!))).toEqual({ normal: null, saver: null })
})

test('quality when the browser asks to save data is a choice of its own; a channel is offered none', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(Navigator.prototype, 'connection', { configurable: true, get: () => ({ saveData: true, effectiveType: '4g' }) })
    localStorage.setItem('sh.player.quality.v1', JSON.stringify({ normal: 1080, saver: null }))
  })
  const resolves = await enter(page)
  await play(page, CONVERTED)
  // The choice for ordinary connections is not the one in force here.
  expect(resolves[0]!.max_height ?? null).toBeNull()
  await page.keyboard.press('s')
  const menu = page.getByTestId('player-menu')
  await expect(menu).toContainText('Your browser asks to save data')
  await menu.getByTestId('player-quality').filter({ hasText: '480p' }).click()
  await expect.poll(() => resolves.at(-1)!.max_height).toBe(480)
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('sh.player.quality.v1')!))).toEqual({ normal: 1080, saver: 480 })
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  // A channel is delivered as it is: its settings have no quality, and its resolve carries no cap.
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('player')).toHaveCount(0)
  await play(page, 'ch:s_xt:1001')
  expect(resolves.at(-1)!.max_height ?? null).toBeNull()
  await page.keyboard.press('s')
  await expect(menu).toBeVisible()
  await expect(menu.getByTestId('player-quality')).toHaveCount(0)
})

test('a live channel keeps its keys: Up and Down zap, digits are channel numbers, and nothing offers a speed', async ({ page }) => {
  await enter(page)
  await play(page, 'ch:s_xt:1001')
  const path = () => new URL(page.url()).pathname
  const first = path()
  await page.keyboard.press('ArrowUp')
  await expect.poll(path).not.toBe(first)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  expect(await video(page, (v) => v.volume)).toBe(1)
  await expect(page.getByTestId('scrub-track')).toHaveCount(0)
  await page.keyboard.press('Shift+>')
  expect(await video(page, (v) => v.playbackRate)).toBe(1)
  await page.keyboard.press('m')
  await expect(page.getByTestId('player-flash')).toHaveText('Muted')
  await page.keyboard.press('s')
  await expect(page.getByTestId('player-menu')).toBeVisible()
  await expect(page.getByTestId('player-speed')).toHaveCount(0)
  await page.keyboard.press('Escape')
  await page.keyboard.press('Shift+?')
  await expect(page.getByTestId('player-shortcuts')).toContainText('Next or previous channel')
  await expect(page.getByTestId('player-shortcuts')).not.toContainText('Slower or faster')
})

test.describe('on a phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true })

  test('the bar is two rows of finger-sized targets, a tap shows and hides it, and two taps on a side skip ten seconds', async ({ page }) => {
    const resolves = await enter(page)
    await play(page, CONVERTED)
    const bar = page.getByTestId('player-bar')
    const boxes = await bar.locator('.pbar-row .fx').evaluateAll((all) => all.map((el) => { const r = el.getBoundingClientRect(); return { top: Math.round(r.top), width: r.width, height: r.height, right: r.right } }))
    expect(new Set(boxes.map((b) => b.top)).size).toBe(2)
    for (const box of boxes) {
      expect(box.width).toBeGreaterThanOrEqual(44)
      expect(box.height).toBeGreaterThanOrEqual(44)
      expect(box.right).toBeLessThanOrEqual(390)
    }
    expect((await track(page).boundingBox())!.height).toBeGreaterThanOrEqual(44)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)

    // Left alone the controls hide; one tap on the picture brings them back and another puts them away.
    const picture = (await page.getByTestId('video').boundingBox())!
    const centre = { x: picture.x + picture.width / 2, y: picture.y + picture.height * 0.4 }
    await expect(ui(page)).toHaveAttribute('data-show', 'false', { timeout: 6000 })
    await page.touchscreen.tap(centre.x, centre.y)
    await expect(ui(page)).toHaveAttribute('data-show', 'true')
    await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
    await page.waitForTimeout(400)
    await page.touchscreen.tap(centre.x, centre.y)
    await expect(ui(page)).toHaveAttribute('data-show', 'false')

    // Two taps on the right third: ten seconds on, with a hint; a third tap makes it twenty. One seek in the end.
    const right = { x: picture.x + picture.width * 0.85, y: centre.y }
    await page.waitForTimeout(400)
    const local = await video(page, (v) => v.currentTime)
    await page.touchscreen.tap(right.x, right.y)
    await page.touchscreen.tap(right.x, right.y)
    await expect(page.getByTestId('seek-hint')).toHaveText('+10 s')
    await expect(page.getByTestId('seek-hint')).toHaveAttribute('data-side', 'right')
    await page.touchscreen.tap(right.x, right.y)
    await expect(page.getByTestId('seek-hint')).toHaveText('+20 s')
    await expect(page.getByTestId('seek-hint')).toHaveCount(0)
    expect((await seeks(page)).at(-1)).toBeGreaterThanOrEqual(local + 19)
    expect(resolves).toHaveLength(1)
    const left = { x: picture.x + picture.width * 0.1, y: centre.y }
    await page.touchscreen.tap(left.x, left.y)
    await page.touchscreen.tap(left.x, left.y)
    await expect(page.getByTestId('seek-hint')).toHaveText('−10 s')
    await expect(page.getByTestId('seek-hint')).toHaveAttribute('data-side', 'left')
  })

  test('a finger drags the scrubber: the target follows it and one seek is made when it lifts', async ({ page }) => {
    const resolves = await enter(page)
    await play(page, CONVERTED)
    // The controls are up when a film starts; a touch on them keeps them up.
    await expect(ui(page)).toHaveAttribute('data-show', 'true')
    const cdp = await page.context().newCDPSession(page)
    const touch = (type: 'touchStart' | 'touchMove' | 'touchEnd', at?: { x: number; y: number }) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: at ? [{ x: Math.round(at.x), y: Math.round(at.y) }] : [] })
    await touch('touchStart', await on(page, 0.2))
    for (const fraction of [0.3, 0.45, 0.6]) await touch('touchMove', await on(page, fraction))
    await expect(page.getByTestId('scrub-ghost')).toBeVisible()
    await expect(page.getByTestId('scrub-bubble')).toHaveText(/^1:0[67]:\d\d$/)
    await page.waitForTimeout(700)
    expect(resolves).toHaveLength(1)
    await touch('touchEnd')
    await expect.poll(() => resolves.length).toBe(2)
    expect(Math.abs(resolves[1]!.start_position_ms! / 1000 - LENGTH_S * 0.6)).toBeLessThan(20)
    await expect(page.getByTestId('scrub-bubble')).toHaveCount(0)
  })
})

test('a film the browser plays as it is has the same bar, and a click on the picture pauses it', async ({ page }) => {
  const resolves = await enter(page)
  await play(page, FILM)
  await page.mouse.move(600, 300)
  await expect(ui(page)).toHaveAttribute('data-show', 'true')
  await expect(page.getByTestId('player-time')).toHaveText(/\/ 0:30$/)
  await expect(page.getByTestId('player-bar').getByRole('button', { name: 'Settings', exact: true })).toBeVisible()
  await expect(page.getByRole('slider', { name: /^Seek\. / })).toHaveAttribute('aria-valuemax', '30')
  // With the controls showing, the picture between the bars is still the picture: a click pauses, another plays.
  await page.mouse.click(600, 300)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'paused')
  await page.mouse.click(600, 300)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  // A step in a file the element holds is the element's own seek: no new stream, however many presses.
  for (let i = 0; i < 2; i++) await page.keyboard.press('l')
  await expect(page.getByTestId('seek-hint')).toHaveText('+20 s')
  await expect.poll(async () => (await seeks(page)).length).toBeGreaterThan(0)
  await expect(page.getByTestId('seek-hint')).toHaveCount(0)
  expect(resolves).toHaveLength(1)
})
