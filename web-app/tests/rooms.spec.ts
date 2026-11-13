import { expect, test, type Browser, type Page } from '@playwright/test'
import { signIn } from './helpers'

// A film only the library has: playable here, so it can be watched together.
const FILM = 'tmdb:movie:110'
const FILM_PATH = `/title/${encodeURIComponent(FILM)}`
// A film only the provider has: the fake server gives its room one shared stream, restarted at every seek.
const PROVIDER_FILM_PATH = `/title/${encodeURIComponent('tmdb:movie:108')}`
// A library film every profile of the fake server is 20 s into on their own.
const WATCHED_FILM_PATH = `/title/${encodeURIComponent('tmdb:movie:103')}`
const CODE = /^[A-HJ-NP-Z2-9]{6}$/

type MockWindow = { __mockRoomIn?: (envelope: string) => void }
/** Something the room server does on its own: a member joining elsewhere, a wait, the room closing. */
const server = (page: Page, code: string, change: Record<string, unknown>) =>
  page.evaluate(([c, ch]) => (window as unknown as MockWindow).__mockRoomIn?.(JSON.stringify({ code: c, change: ch })), [code, change] as const)

/** A signed-in browser of its own (a second person), with the first-run notice already accepted. */
async function person(browser: Browser, id: 'p2' | 'p3', name: string, phone = false): Promise<Page> {
  const context = await browser.newContext(phone ? { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true } : { viewport: { width: 1280, height: 720 } })
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

/** Where a player is in the title: for a stream that starts part-way in, the start the player was told plus the media time. */
const clock = (page: Page) => page.evaluate(() => {
  const video = document.querySelector('video')!
  const from = Number(document.querySelector('[data-testid="player"]')?.getAttribute('data-stream-start') ?? 0) / 1000
  // An element asked to play says "not paused" at once; it is only somewhere once it has data and is not mid-seek.
  return { at: Date.now(), time: from + video.currentTime, paused: video.paused, settled: video.readyState >= 2 && !video.seeking }
})
/** The room calls a page makes: seats taken, descriptors asked for, and how each was authorized. */
function calls(page: Page) {
  const seen = { joins: 0, descriptors: [] as Array<{ token: string | null; bearer: boolean }> }
  page.on('request', (r) => {
    const url = new URL(r.url())
    if (r.method() === 'POST' && url.pathname.endsWith('/join')) seen.joins++
    if (r.method() === 'GET' && url.pathname.endsWith('/descriptor')) seen.descriptors.push({ token: url.searchParams.get('member_token'), bearer: 'authorization' in r.headers() })
  })
  return seen
}
/** How often the player loaded a stream, and every position it was moved to. On the page as it is, and after a reload. */
async function watchPlayer(page: Page) {
  const install = () => {
    const w = window as unknown as { __loads?: number; __seeks?: number[] }
    if (w.__seeks) return
    w.__loads = 0
    const seeks: number[] = w.__seeks = []
    document.addEventListener('loadstart', () => { w.__loads!++ }, true)
    const time = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime')!
    Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', { ...time, set(value: number) { seeks.push(value); time.set!.call(this, value) } })
  }
  await page.addInitScript(install)
  await page.evaluate(install)
}
const loads = (page: Page) => page.evaluate(() => (window as unknown as { __loads: number }).__loads)
/** The seats the fake server has in a room. */
const seatsOf = (page: Page, code: string) => page.evaluate((c) => (JSON.parse(localStorage.getItem('mock.rooms')!) as Record<string, { members: Array<{ id: string; name: string }> }>)[c]!.members.map((m) => m.id), code)
/** How far apart the two players are, in seconds, allowing for the two readings not being simultaneous. */
async function apart(a: Page, b: Page): Promise<number> {
  const [x, y] = await Promise.all([clock(a), clock(b)])
  return Math.abs(x.time - y.time - (x.paused || y.paused ? 0 : (x.at - y.at) / 1000))
}
async function together(a: Page, b: Page, playing: boolean, near?: number) {
  await expect.poll(async () => {
    const [x, y] = await Promise.all([clock(a), clock(b)])
    return x.settled && y.settled && x.paused === !playing && y.paused === !playing && (near === undefined || Math.abs(x.time - near) < 1.5)
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

test('a room on one shared stream loads its playlist again when the stream epoch changes, and never takes another seat', async ({ browser }) => {
  test.setTimeout(90_000)
  const host = await person(browser, 'p2', 'Guest')
  const guest = await person(browser, 'p3', 'Kids')
  await link(host, guest)
  await watchPlayer(host); await watchPlayer(guest)
  const seen = { host: calls(host), guest: calls(guest) }
  const code = await startRoom(host, PROVIDER_FILM_PATH)
  await host.getByTestId('room-start').click()
  await expect(host.getByTestId('player')).toBeVisible()
  await joinByCode(guest, code)
  await guest.getByTestId('room-join-go').click()
  await expect(guest.getByTestId('room-member')).toHaveCount(2)
  await together(host, guest, false, 0)
  const seats = await seatsOf(host, code)
  const urls = await Promise.all([host, guest].map((page) => page.evaluate(() => document.querySelector('video')!.currentSrc)))
  expect(await Promise.all([loads(host), loads(guest)])).toEqual([1, 1])

  // A seek restarts the room's stream. The state says so (a new epoch and start): both members load the URL they hold again.
  await host.keyboard.press('ArrowRight')
  await together(host, guest, false, 10)
  for (const page of [host, guest]) {
    await expect(page.getByTestId('player')).toHaveAttribute('data-stream-start', '10000')
    await expect(page.getByTestId('room-member')).toHaveCount(2)
    expect(await loads(page)).toBe(2)
  }
  expect(await Promise.all([host, guest].map((page) => page.evaluate(() => document.querySelector('video')!.currentSrc)))).toEqual(urls)
  // Play and pause are not seeks: the stream stays.
  await guest.keyboard.press('k')
  await together(host, guest, true)
  await host.keyboard.press('k')
  await together(host, guest, false)
  expect(await Promise.all([loads(host), loads(guest)])).toEqual([2, 2])
  // Seeking while playing, from the guest.
  await guest.keyboard.press('k')
  await together(host, guest, true)
  await guest.keyboard.press('ArrowRight')
  // Both follow the new start (20 s, or 25 s if the room had played past 15 s) before they are compared.
  const starts = () => Promise.all([host, guest].map((page) => page.getByTestId('player').getAttribute('data-stream-start')))
  await expect.poll(async () => { const [a, b] = await starts(); return a === b && a !== '10000' }).toBe(true)
  await together(host, guest, true)
  expect((await clock(host)).time).toBeGreaterThan(20)
  expect(await Promise.all([loads(host), loads(guest)])).toEqual([3, 3])
  // Nobody joined a second time, nobody asked for a descriptor, and the two seats are the ones taken at the start.
  expect(seen.host.joins).toBe(1)
  expect(seen.guest.joins).toBe(1)
  expect(seen.host.descriptors).toEqual([])
  expect(seen.guest.descriptors).toEqual([])
  expect(await seatsOf(host, code)).toEqual(seats)
})

test('a member joining a library-title room starts at the room position, not at their own resume point', async ({ browser }) => {
  test.setTimeout(90_000)
  const host = await person(browser, 'p2', 'Guest')
  const guest = await person(browser, 'p3', 'Kids')
  await link(host, guest)
  await watchPlayer(guest)
  const seen = { host: calls(host), guest: calls(guest) }
  const code = await startRoom(host, WATCHED_FILM_PATH)
  await host.getByTestId('room-start').click()
  await expect(host.getByTestId('player')).toHaveAttribute('data-state', 'paused')
  // The room is playing, a few seconds in, when the guest arrives.
  await host.keyboard.press('ArrowRight')
  await host.keyboard.press('k')
  await expect(host.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  await joinByCode(guest, code)
  const joined = guest.waitForResponse((r) => r.request().method() === 'POST' && r.url().endsWith('/join'))
  await guest.getByTestId('room-join-go').click()
  const { descriptor } = await (await joined).json() as { descriptor: { resume_position_ms: number; stream_epoch: number | null } }
  expect(descriptor.stream_epoch).toBeNull()
  expect(descriptor.resume_position_ms).toBeGreaterThanOrEqual(10_000)
  await expect(guest.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  // The first place the guest's player went is the one the join named: the room's, not 0 and not their own 20 s.
  expect((await guest.evaluate(() => (window as unknown as { __seeks: number[] }).__seeks))[0]).toBe(descriptor.resume_position_ms / 1000)
  // Started there, the guest is within the room's one-second tolerance of its clock, and so within two of the host.
  expect(await apart(host, guest)).toBeLessThan(2)

  // A seek in such a room is a state and nothing else: each player moves in the file it has.
  await host.keyboard.press('ArrowLeft')
  await together(host, guest, true)
  expect(await loads(guest)).toBe(1)
  expect(seen.host.joins).toBe(1)
  expect(seen.guest.joins).toBe(1)
  expect(seen.host.descriptors).toEqual([])
  expect(seen.guest.descriptors).toEqual([])
})

test('a player that lost its place asks for its descriptor with the member token; a reload and a token renewal keep the seat', async ({ page }) => {
  test.setTimeout(60_000)
  await signIn(page)
  await page.evaluate(() => localStorage.setItem('mock.roomTokenLife', '10'))
  const seen = calls(page)
  const code = await startRoom(page)
  const first = page.waitForResponse((r) => r.request().method() === 'POST' && r.url().endsWith('/join'))
  await page.getByTestId('room-start').click()
  const seat = await (await first).json() as { member_token: string; member_id: string }
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'paused')
  await page.keyboard.press('ArrowRight')
  await expect.poll(async () => (await clock(page)).time).toBeCloseTo(10, 0)

  // The player fails: it comes back with GET descriptor (no bearer token, the member token in the query), at the room's position.
  await page.evaluate(() => document.querySelector('video')!.dispatchEvent(new Event('error')))
  await expect.poll(() => seen.descriptors.length).toBe(1)
  expect(seen.descriptors[0]).toEqual({ token: seat.member_token, bearer: false })
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'paused')
  await expect.poll(async () => (await clock(page)).time).toBeCloseTo(10, 0)
  expect(seen.joins).toBe(1)
  expect(await seatsOf(page, code)).toEqual([seat.member_id])

  // Before the token runs out the client joins with it: the fake server would seat a join without it a second time.
  await expect.poll(() => seen.joins, { timeout: 15_000 }).toBe(2)
  await expect(page.getByTestId('room-member')).toHaveText(['Guest (host)'])
  expect(await seatsOf(page, code)).toEqual([seat.member_id])
  expect(await page.evaluate((c) => (JSON.parse(sessionStorage.getItem('sh.roomSeats.v1')!) as Record<string, string>)[c], code)).not.toBe(seat.member_token)

  // The page is loaded again while the seat is still held: the join carries the token kept for this tab, and keeps the seat.
  const before = seen.joins
  await page.reload()
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'paused')
  await expect(page.getByTestId('room-member')).toHaveText(['Guest (host)'])
  expect(seen.joins).toBeGreaterThan(before)
  expect(await seatsOf(page, code)).toEqual([seat.member_id])
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
  // A seat the room gave up is not one to come back to: its token is forgotten.
  await expect(page.getByTestId('room-closed')).toContainText('Your connection was quiet for too long')
  expect(await page.evaluate(() => sessionStorage.getItem('sh.roomSeats.v1'))).toBe('{}')
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

test('Watch together needs rooms.create and Join a room needs rooms.join', async ({ browser }) => {
  // The Kid role may join rooms, not start them.
  const kid = await person(browser, 'p3', 'Kids')
  await kid.goto(FILM_PATH)
  await expect(kid.getByTestId('title-play')).toBeVisible()
  await expect(kid.getByTestId('title-together')).toHaveCount(0)
  await kid.getByTestId('title-play').click()
  await expect(kid.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  await kid.keyboard.press('m')
  await expect(kid.getByTestId('player-menu')).toBeVisible()
  await expect(kid.getByTestId('player-together')).toHaveCount(0)
  await kid.goto('/search')
  await expect(kid.getByTestId('search-join-room')).toBeVisible()
  await kid.getByTestId('nav-more').click()
  await expect(kid.getByTestId('nav-join-room')).toBeVisible()

  // Someone else's room, and a role that loses rooms.join: nothing offers a room, and a link says why.
  const host = await person(browser, 'p2', 'Guest')
  await link(host, kid)
  const code = await startRoom(host)
  await kid.evaluate(() => {
    const db = JSON.parse(localStorage.getItem('mock.people.v1')!) as { roles: Array<{ id: string; permissions: string[] }> }
    const role = db.roles.find((r) => r.id === 'kid')!
    role.permissions = role.permissions.filter((p) => p !== 'rooms.join')
    localStorage.setItem('mock.people.v1', JSON.stringify(db))
  })
  await kid.goto('/search')
  await expect(kid.getByTestId('search-controls')).toBeVisible()
  await expect(kid.getByTestId('search-join-room')).toHaveCount(0)
  await kid.getByTestId('nav-more').click()
  await expect(kid.getByTestId('more-menu')).toBeVisible()
  await expect(kid.getByTestId('nav-join-room')).toHaveCount(0)
  await kid.goto(`/room/${code}`)
  await expect(kid.getByTestId('room-refusal')).toContainText('not allowed to join watch rooms')
  await expect(kid.getByTestId('room-join-go')).toHaveCount(0)
  await expect(kid.getByTestId('room-other')).toHaveCount(0)
  await expect(kid.getByRole('button', { name: 'Not now' })).toHaveAttribute('data-focused', 'true')
})

test('the host ends the room for everyone, after being asked', async ({ browser }) => {
  const host = await person(browser, 'p2', 'Guest')
  const guest = await person(browser, 'p3', 'Kids')
  await link(host, guest)
  const code = await startRoom(host)
  await host.getByTestId('room-start').click()
  await expect(host.getByTestId('player')).toBeVisible()
  await joinByCode(guest, code)
  await guest.getByTestId('room-join-go').click()
  await expect(host.getByTestId('room-member')).toHaveCount(2)
  await together(host, guest, false, 0)

  // Only the host is offered it, from the same place as leaving.
  await guest.keyboard.press('Escape')
  await expect(guest.getByTestId('room-leave-confirm')).toBeVisible()
  await expect(guest.getByTestId('room-end')).toHaveCount(0)
  await guest.keyboard.press('Enter')
  await expect(guest.getByTestId('room-leave-confirm')).toHaveCount(0)

  // By remote: Back, across to End room for everyone, and the question starts on the safe answer.
  await host.keyboard.press('Escape')
  await expect(host.getByRole('button', { name: 'Stay' })).toHaveAttribute('data-focused', 'true')
  await host.keyboard.press('ArrowRight')
  await host.keyboard.press('ArrowRight')
  await expect(host.getByTestId('room-end')).toHaveAttribute('data-focused', 'true')
  await host.keyboard.press('Enter')
  await expect(host.getByTestId('room-end-confirm')).toContainText('Playback stops for all 2 of you.')
  await expect(host.getByRole('button', { name: 'Keep the room' })).toHaveAttribute('data-focused', 'true')
  await host.keyboard.press('Enter')
  await expect(host.getByTestId('room-end-confirm')).toHaveCount(0)
  await expect(guest.getByTestId('room-closed')).toHaveCount(0)

  await host.keyboard.press('Escape')
  await host.getByTestId('room-end').click()
  await expect(host.getByRole('button', { name: 'Keep the room' })).toHaveAttribute('data-focused', 'true')
  await host.keyboard.press('ArrowRight')
  await expect(host.getByTestId('room-end-go')).toHaveAttribute('data-focused', 'true')
  await host.keyboard.press('Enter')
  // The host is back on the title, without being told their own room closed; the guest is told.
  await expect(host).toHaveURL(new RegExp(`${encodeURIComponent(FILM)}$`))
  await expect(host.getByTestId('title-detail')).toBeVisible()
  await expect(host.getByTestId('room-closed')).toHaveCount(0)
  await expect(guest.getByTestId('room-closed')).toContainText('The host closed the room.')
  await host.goto(`/room/${code}`)
  await expect(host.getByTestId('room-title')).toHaveText('Room not found')
})

test('on a phone the room player keeps back and title on one row; the members are a count that opens their list', async ({ browser }) => {
  const page = await person(browser, 'p2', 'Guest', true)
  const code = await startRoom(page)
  await page.getByTestId('room-start').click()
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'paused')
  const names = ['Marisol', 'Viewer Two', 'Tomasz the Third', 'Aiko', 'Benedikt']
  for (const [i, name] of names.entries()) await server(page, code, { op: 'join', member: { id: `m${i}`, profile_id: `x${i}`, name, host: false, buffering: i === 1, seen_ms: Date.now() + 120_000 } })
  const count = page.getByTestId('room-count')
  await expect(count).toHaveText('6')
  await expect(count).toHaveAttribute('aria-label', /^6 in the room: Guest, Marisol, /)
  // One row: the bar is no taller than its back button and padding, and nothing runs off the side.
  const top = await page.locator('.player-ui .top').boundingBox()
  expect(top!.height).toBeLessThan(110)
  const title = await page.getByTestId('player-title').boundingBox()
  const chip = await count.boundingBox()
  expect(Math.abs((title!.y + title!.height / 2) - (chip!.y + chip!.height / 2))).toBeLessThan(24)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await expect(page.getByTestId('room-members')).toHaveCount(0)

  await count.tap()
  const list = page.getByTestId('room-member-list')
  await expect(list.getByTestId('room-member')).toHaveText(['GuestHostYou', 'Marisol', 'Viewer Twobuffering', 'Tomasz the Third', 'Aiko', 'Benedikt'])
  await expect(list.getByRole('button', { name: 'Done' })).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Escape')
  await expect(list).toHaveCount(0)
  await expect(page.getByTestId('player')).toBeVisible()
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
