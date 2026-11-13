import { expect, test, type Browser, type Page } from '@playwright/test'
import { signIn } from './helpers'

// A film only the library has: playable here, so it can be watched together.
const FILM = 'tmdb:movie:110'
const FILM_PATH = `/title/${encodeURIComponent(FILM)}`
// A film only the provider has: the fake server gives its room one shared stream, restarted at every seek.
const PROVIDER_FILM_PATH = `/title/${encodeURIComponent('tmdb:movie:108')}`
const CODE = /^[A-HJ-NP-Z2-9]{6}$/

type MockWindow = { __mockRoomIn?: (envelope: string) => void }
/** Something the room server does on its own: a member joining elsewhere, a wait, the room closing. */
const server = (page: Page, code: string, change: Record<string, unknown>) =>
  page.evaluate(([c, ch]) => (window as unknown as MockWindow).__mockRoomIn?.(JSON.stringify({ code: c, change: ch })), [code, change] as const)

/** A signed-in browser of its own (a second person), with the first-run notice already accepted. */
async function person(browser: Browser, id: 'p2' | 'p3', name: string): Promise<Page> {
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 } })
  await context.addInitScript(([profileId, profileName]) => {
    const profile = { id: profileId, name: profileName, avatar: null, is_admin: false, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' }
    sessionStorage.setItem('mock.accepted', JSON.stringify(['p1', 'p2', 'p3']))
    if (!localStorage.getItem('sh.session.v1')) localStorage.setItem('sh.session.v1', JSON.stringify({ access: `mock-access-${profileId}`, refresh: `mock-refresh-${profileId}`, profile }))
  }, [id, name] as const)
  const page = await context.newPage()
  await page.goto('/')
  await expect(page.getByTestId('home')).toBeVisible()
  return page
}

/** Two browsers share nothing, so the test carries the fake room server's changes from each to the other. */
async function link(a: Page, b: Page) {
  const carry = (to: Page) => (envelope: string) => to.evaluate((e) => (window as unknown as MockWindow).__mockRoomIn?.(e), envelope).catch(() => undefined)
  await a.exposeFunction('__mockRoomOut', carry(b))
  await b.exposeFunction('__mockRoomOut', carry(a))
}

async function startRoom(page: Page, path = FILM_PATH): Promise<string> {
  await page.goto(path)
  await page.getByTestId('title-together').click()
  await expect(page.getByTestId('room-share')).toBeVisible()
  const code = await page.getByTestId('room-code').innerText()
  expect(code).toMatch(CODE)
  return code
}

async function joinByCode(page: Page, code: string) {
  await page.getByTestId('nav-more').click()
  await page.getByTestId('nav-join-room').click()
  await expect(page.getByLabel('Room code')).toBeFocused()
  await page.keyboard.type(code.toLowerCase())
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(new RegExp(`/room/${code}$`))
  await expect(page.getByTestId('room-join')).toBeVisible()
}

/** Where a player is in the title: for a stream that starts part-way in, the descriptor's start plus the media time. */
const clock = (page: Page) => page.evaluate(() => {
  const video = document.querySelector('video')!
  const from = Number(new URL(video.currentSrc, location.href).searchParams.get('from') ?? 0) / 1000
  return { at: Date.now(), time: from + video.currentTime, paused: video.paused }
})
/** How far apart the two players are, in seconds, allowing for the two readings not being simultaneous. */
async function apart(a: Page, b: Page): Promise<number> {
  const [x, y] = await Promise.all([clock(a), clock(b)])
  return Math.abs(x.time - y.time - (x.paused || y.paused ? 0 : (x.at - y.at) / 1000))
}
async function together(a: Page, b: Page, playing: boolean, near?: number) {
  await expect.poll(async () => {
    const [x, y] = await Promise.all([clock(a), clock(b)])
    return x.paused === !playing && y.paused === !playing && (near === undefined || Math.abs(x.time - near) < 1.5)
  }, { timeout: 15_000 }).toBe(true)
  // Not one lucky reading: they stay together.
  for (let i = 0; i < 4; i++) {
    expect(await apart(a, b)).toBeLessThan(1)
    await a.waitForTimeout(400)
  }
}

test('Watch together makes a room and shows how to share it', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  await signIn(page)
  const code = await startRoom(page)
  const sheet = page.getByTestId('room-share')
  await expect(sheet.getByTestId('room-qr')).toBeVisible()
  await expect(sheet.getByTestId('room-link')).toHaveText(new RegExp(`/room/${code}$`))
  await expect(sheet.getByTestId('room-start')).toHaveAttribute('data-focused', 'true')
  await sheet.getByTestId('room-copy').click()
  await expect(page.getByText('Link copied.')).toBeVisible()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toMatch(new RegExp(`/room/${code}$`))

  await sheet.getByTestId('room-start').click()
  await expect(page).toHaveURL(new RegExp(`/room/${code}`))
  await expect(page.getByTestId('player')).toBeVisible()
  await expect(page.getByTestId('room-member')).toHaveText(['Guest (host)'])
  await expect(page.getByTestId('room-lock')).toBeVisible()
})

test('a room is joined by its code, from More and from Search', async ({ browser }) => {
  const host = await person(browser, 'p2', 'Guest')
  const guest = await person(browser, 'p3', 'Kids')
  await link(host, guest)
  const code = await startRoom(host)
  await host.getByTestId('room-start').click()
  await expect(host.getByTestId('room-member')).toHaveCount(1)

  // Search carries the same entry; a code that cannot be one is explained, not sent.
  await guest.getByTestId('nav-search').click()
  await guest.getByTestId('search-join-room').click()
  await expect(guest.getByLabel('Room code')).toBeFocused()
  await guest.keyboard.type('abc10')
  await expect(guest.getByTestId('join-room-go')).toHaveAttribute('aria-disabled', 'true')
  await guest.keyboard.type('o')
  await guest.keyboard.press('Enter')
  await expect(guest.getByTestId('join-room').getByRole('alert')).toContainText('never contains 0, O, 1 or I')
  await guest.keyboard.press('Escape')
  await expect(guest.getByTestId('join-room')).toHaveCount(0)

  await joinByCode(guest, code)
  await expect(guest.getByTestId('room-title')).toHaveText('Ember Road')
  await expect(guest.getByTestId('room-host')).toContainText('Guest started this room.')
  await expect(guest.getByTestId('room-join-members')).toContainText('Guest (host)')
  await expect(guest.getByTestId('room-join-go')).toHaveAttribute('data-focused', 'true')
  await guest.keyboard.press('Enter')
  await expect(guest.getByTestId('player')).toBeVisible()
  await expect(guest.getByTestId('room-member')).toHaveText(['Guest (host)', 'Kids'])
  await expect(host.getByTestId('room-member')).toHaveText(['Guest (host)', 'Kids'])
  // Only the host is offered the lock.
  await expect(guest.getByTestId('room-lock')).toHaveCount(0)

  // Leaving asks first; the others see the seat empty.
  await guest.getByTestId('room-leave').click()
  await guest.getByTestId('room-leave-go').click()
  await expect(guest).toHaveURL(new RegExp(`${encodeURIComponent(FILM)}$`))
  await expect(host.getByTestId('room-member')).toHaveText(['Guest (host)'])
})

test('two browsers in one room stay within a second across play, pause and seek', async ({ browser }) => {
  test.setTimeout(90_000)
  const host = await person(browser, 'p2', 'Guest')
  const guest = await person(browser, 'p3', 'Kids')
  await link(host, guest)
  const code = await startRoom(host)
  await host.getByTestId('room-start').click()
  await expect(host.getByTestId('player')).toBeVisible()
  await joinByCode(guest, code)
  await guest.getByTestId('room-join-go').click()
  await expect(guest.getByTestId('room-member')).toHaveCount(2)
  await together(host, guest, false, 0)

  // Play, from the host.
  await host.keyboard.press('k')
  await together(host, guest, true)
  // Pause, from the guest: the room stops for both.
  await guest.keyboard.press('k')
  await together(host, guest, false)
  const paused = (await clock(host)).time
  // Seek while paused, from the host: ten seconds on, for both.
  await host.keyboard.press('ArrowRight')
  await together(host, guest, false, paused + 10)
  // Play again from the guest, then seek back while playing.
  await guest.keyboard.press('k')
  await together(host, guest, true)
  const playing = (await clock(guest)).time
  await guest.keyboard.press('ArrowLeft')
  await together(host, guest, true)
  expect((await clock(host)).time).toBeLessThan(playing)

  // Only I can control: the guest's controls explain themselves and change nothing.
  await host.mouse.move(300, 300)
  await host.getByTestId('room-lock').click()
  await expect(host.getByTestId('room-lock')).toHaveAttribute('aria-pressed', 'true')
  await expect(guest.getByTestId('room-locked')).toHaveCount(1)
  await guest.keyboard.press('k')
  await expect(guest.getByText('Only the host can control playback in this room.')).toBeVisible()
  await together(host, guest, true)
  await host.keyboard.press('k')
  await together(host, guest, false)
})

test('a room on one shared stream fetches a fresh descriptor at every seek and stays together', async ({ browser }) => {
  test.setTimeout(90_000)
  const host = await person(browser, 'p2', 'Guest')
  const guest = await person(browser, 'p3', 'Kids')
  await link(host, guest)
  const joins = { host: 0, guest: 0 }
  host.on('request', (r) => { if (r.method() === 'POST' && r.url().endsWith('/join')) joins.host++ })
  guest.on('request', (r) => { if (r.method() === 'POST' && r.url().endsWith('/join')) joins.guest++ })
  const code = await startRoom(host, PROVIDER_FILM_PATH)
  await host.getByTestId('room-start').click()
  await expect(host.getByTestId('player')).toBeVisible()
  await joinByCode(guest, code)
  await guest.getByTestId('room-join-go').click()
  await expect(guest.getByTestId('room-member')).toHaveCount(2)
  await together(host, guest, false, 0)
  expect(joins).toEqual({ host: 1, guest: 1 })

  // A seek restarts the room's stream: both members ask for it again, once, and take their new seats.
  await host.keyboard.press('ArrowRight')
  await together(host, guest, false, 10)
  await expect.poll(() => joins).toEqual({ host: 2, guest: 2 })
  for (const page of [host, guest]) {
    await expect(page.getByTestId('room-member')).toHaveCount(2)
    await expect(page.getByTestId('room-members')).toContainText('Guest (host)')
    await expect(page.getByTestId('room-members')).toContainText('Kids')
    expect(await page.evaluate(() => new URL(document.querySelector('video')!.currentSrc).searchParams.get('from'))).toBe('10000')
  }
  // Play and pause are not seeks: the stream stays.
  await guest.keyboard.press('k')
  await together(host, guest, true)
  await host.keyboard.press('k')
  await together(host, guest, false)
  expect(joins).toEqual({ host: 2, guest: 2 })
  // Seeking while playing, from the guest.
  await guest.keyboard.press('k')
  await together(host, guest, true)
  await guest.keyboard.press('ArrowRight')
  await together(host, guest, true)
  expect((await clock(host)).time).toBeGreaterThan(20)
  await expect.poll(() => joins).toEqual({ host: 3, guest: 3 })
})

test('a channel is watched together from the player menu: play and pause, no seeking, no zapping', async ({ page }) => {
  await signIn(page)
  await page.goto(`/play/${encodeURIComponent('ch:s_xt:1002')}`)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  await page.keyboard.press('m')
  await expect(page.getByTestId('player-together')).toBeVisible()
  await page.getByTestId('player-together').click()
  await expect(page.getByTestId('room-share')).toBeVisible()
  const code = await page.getByTestId('room-code').innerText()
  await expect(page.getByTestId('room-start')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(new RegExp(`/room/${code}`))
  await expect(page.getByTestId('room-member')).toHaveText(['Guest (host)'])
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  // Up and Down belong to the control bar here, not to the next channel.
  await page.keyboard.press('ArrowUp')
  await page.keyboard.press('ArrowDown')
  await expect(page).toHaveURL(new RegExp(`/room/${code}`))
  await expect(page.getByTestId('player-list-btn')).toHaveCount(0)
  await expect(page.getByTestId('scrub-track')).toHaveCount(0)
  // The control bar has focus on Pause: the room pauses, and with it this player.
  await expect(page.getByRole('button', { name: 'Pause', exact: true })).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'paused')
  await expect(page.getByRole('button', { name: 'Play', exact: true })).toHaveAttribute('data-focused', 'true')
  // The room made from inside a room is the room itself: Invite shows the same code.
  await page.getByTestId('room-invite').click()
  await expect(page.getByTestId('room-code')).toHaveText(code)
})

test('the room explains a wait, asks before Back leaves, and says why it ended', async ({ page }) => {
  await signIn(page)
  const code = await startRoom(page)
  await page.getByTestId('room-start').click()
  await expect(page.getByTestId('room-member')).toHaveCount(1)

  // Someone joins on a slow connection and the room waits for them.
  await server(page, code, { op: 'join', member: { id: 'm-slow', profile_id: 'p9', name: 'Marisol', host: false, buffering: true, seen_ms: Date.now() + 120_000 } })
  await expect(page.getByTestId('room-member').nth(1)).toContainText('Marisol')
  await expect(page.getByTestId('room-member').nth(1)).toContainText('buffering')
  await server(page, code, { op: 'say', message: { type: 'waiting', name: 'Marisol' } })
  await expect(page.getByTestId('room-waiting')).toHaveText('Waiting for Marisol…')
  // Any room state ends the wait.
  await page.keyboard.press('k')
  await expect(page.getByTestId('room-waiting')).toHaveCount(0)
  await page.keyboard.press('k')
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'paused')

  // Back does not walk out of the room without asking.
  await page.keyboard.press('Escape')
  await expect(page.getByRole('button', { name: 'Stay' })).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('room-leave-confirm')).toHaveCount(0)
  await expect(page.getByTestId('player')).toBeVisible()

  // The server gives this seat up; Rejoin takes a new one.
  const seat = await page.evaluate((c) => (JSON.parse(localStorage.getItem('mock.rooms')!) as Record<string, { members: Array<{ id: string; name: string }> }>)[c]!.members.find((m) => m.name === 'Guest')!.id, code)
  await server(page, code, { op: 'drop', member_id: seat, reason: 'silent' })
  await expect(page.getByTestId('room-closed')).toContainText('Your connection was quiet for too long')
  await page.getByTestId('room-rejoin').click()
  await expect(page.getByTestId('room-closed')).toHaveCount(0)
  await expect(page.getByTestId('room-member').filter({ hasText: 'Guest (host)' })).toHaveCount(1)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'paused')

  // The host closes the room from elsewhere.
  await server(page, code, { op: 'close', reason: 'deleted' })
  await expect(page.getByTestId('room-closed')).toContainText('The host closed the room.')
  await expect(page.getByRole('button', { name: 'Back to Ember Road' })).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('title-detail')).toBeVisible()
})

test('a room that is gone or full says so on the join screen', async ({ page }) => {
  await signIn(page)
  await page.goto('/room/ZZZZZZ')
  await expect(page.getByTestId('room-title')).toHaveText('Room not found')
  await expect(page.getByTestId('room-join-go')).toHaveCount(0)
  await expect(page.getByTestId('room-other')).toHaveAttribute('data-focused', 'true')

  const code = await startRoom(page)
  for (let i = 0; i < 8; i++) await server(page, code, { op: 'join', member: { id: `m${i}`, profile_id: `x${i}`, name: `Viewer ${i + 1}`, host: false, buffering: false, seen_ms: Date.now() + 120_000 } })
  await page.goto(`/room/${code}`)
  await expect(page.getByTestId('room-join-members')).toContainText('Viewer 8')
  await expect(page.getByTestId('room-join-go')).toHaveText('Room is full')
  await expect(page.getByTestId('room-join-go')).toHaveAttribute('aria-disabled', 'true')
})

test('a room link opened signed out comes back to the room after sign-in', async ({ page }) => {
  await signIn(page)
  const code = await startRoom(page)
  await page.evaluate(() => localStorage.removeItem('sh.session.v1'))
  await page.goto(`/room/${code}`)
  await expect(page.getByTestId('profile-picker')).toBeVisible()
  // A sign-in that leaves the page comes back to the start page, not to the link.
  await page.goto('/')
  await page.getByTestId('profile-card').filter({ hasText: 'Guest' }).click()
  await expect(page).toHaveURL(new RegExp(`/room/${code}$`))
  await expect(page.getByTestId('room-join-go')).toBeVisible()
})
