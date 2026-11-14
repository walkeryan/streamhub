import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'
import { asOwner, fits, listing, stubSports, type StubEvent } from './sports-helpers'

// These run on the fixture line-up (src/mocks/sports.ts): two live games, one of them on two channels,
// games to come today and tomorrow, one slot name left over from weeks ago, a dateless game channel
// and always-on networks whose guide is filler.
const focused = (page: Page) => page.locator('.fx[data-focused="true"]')

test('the screen lists live games, what is coming by day and the networks, and nothing stale or repeated', async ({ page }) => {
  await signIn(page)
  await page.goto('/sports')
  const live = page.getByTestId('sports-live').getByTestId('sports-listing')
  await expect(live).toHaveCount(2)
  await expect(live.nth(0)).toContainText('Riverton Rovers vs Harbor City')
  await expect(live.nth(0)).toContainText('VANTIX+ 004 · Ch 904')
  await expect(live.nth(0)).toContainText('FHD')
  await expect(live.nth(1)).toContainText('Northfield at Lakeside')
  await expect(live.nth(1)).toContainText('2 channels')
  for (const card of [live.nth(0), live.nth(1)]) { await expect(card).toContainText('LIVE'); await expect(card).toContainText(/Started \d{1,2}:\d{2}/) }
  // What is to come: one row of tiles per sport, each game once, with its day, time and channel.
  const row = (sport: string) => page.getByTestId('sports-row').filter({ has: page.getByRole('heading', { name: sport, exact: true }) }).getByTestId('sports-listing')
  await expect(row('Lacrosse')).toHaveText([/Tomorrow.*VANTIX\+ 005 · Ch 905.*Millbrook at Lakeside.*1:00 PM · Lacrosse/])
  await expect(row('Rugby')).toHaveText([/Tomorrow.*2 channels.*Otters vs Herons.*7:30 PM · Rugby/])
  await expect(row("Men's Soccer")).toContainText(['Oakdale at Port Linden'])
  await expect(page.getByTestId('sports-row').getByTestId('sports-listing').filter({ hasText: 'Riverton Rovers' })).toHaveCount(0)
  // A time is the viewer's local time: no zone is named anywhere.
  await expect(page.getByTestId('sports')).not.toContainText(/America\/|UTC|GMT/)
  // The name left over from weeks ago, raw provider names and audio caveats never show.
  await expect(page.getByTestId('sports')).not.toContainText(/Stale Town|\(XX\)|\d{4}-\d{2}-\d{2}|audio|verif/i)
  await expect(page.getByRole('heading', { name: /ended|earlier/i })).toHaveCount(0)
  const channels = page.getByTestId('sports-channel')
  await expect(channels.first()).toContainText('Kestrels at Badgers')
  await expect(channels.first()).toContainText('CORVAN LEAGUE 02 · Ch 921')
  await expect(channels.first()).not.toContainText(/LIVE|Started/)
  // A network shows what its guide really lists; filler that repeats its name is not a programme.
  const combat = channels.filter({ hasText: 'COMBAT' })
  await expect(combat).toHaveText(/^COMBATCh 933HD$/)
  await expect(channels.filter({ hasText: 'Example Football Network' })).toContainText('Ch 934 · Now: Football: Rivermen at Highlanders')
  // Tomorrow's page, then the day after: each lists its own games only.
  await page.getByRole('button', { name: 'Tomorrow', exact: true }).click()
  await expect(page.getByTestId('sports-listing')).toHaveText([/Millbrook at Lakeside/, /Otters vs Herons/])
  await page.getByRole('button', { name: 'Next day', exact: true }).click()
  await expect(page.getByTestId('sports-listing')).toHaveText([/Eastgate Town at Westbrook.*6:00 PM/])
})

test('a live game on one channel plays at once', async ({ page }) => {
  await signIn(page)
  await page.goto('/sports')
  await listing(page, 'Riverton Rovers vs Harbor City').click()
  await expect(page).toHaveURL(/\/play\/ch%3As_xt%3A9004$/)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByTestId('player')).toBeVisible()
  // The title comes from the resolved stream, under the channel's clean name.
  await expect(page.getByTestId('player-title')).toContainText('VANTIX+ 004')
})

test('choosing a channel in the picker reaches the player, also on a plain-http origin', async ({ page }) => {
  // The dead Watch button: a page served over plain http on a LAN address is not a secure context and
  // has no crypto.randomUUID. Playing must not depend on it.
  await page.addInitScript(() => Object.defineProperty(Crypto.prototype, 'randomUUID', { value: undefined, configurable: true }))
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await signIn(page)
  await page.goto('/sports')
  expect(await page.evaluate(() => typeof crypto.randomUUID)).toBe('undefined')
  await listing(page, 'Northfield at Lakeside').click()
  const picker = page.getByTestId('sports-picker')
  await expect(picker.getByRole('heading')).toHaveText('Northfield at Lakeside')
  await expect(picker.getByTestId('sports-feed')).toHaveText([/^VANTIX\+ 001Ch 901 · English$/, /^LUMEX TV 007Ch 915 · EnglishHD$/])
  const resolved = page.waitForRequest((request) => request.method() === 'POST' && request.url().endsWith('/api/v1/playback/resolve'))
  await picker.getByRole('button', { name: 'Watch on LUMEX TV 007', exact: true }).click()
  await expect(page).toHaveURL(/\/play\/ch%3As_xt%3A9015$/)
  await resolved
  await expect(page.getByTestId('player-title')).toContainText('LUMEX TV 007')
  expect(errors).toEqual([])
})

test('a remote alone goes from the screen to a picker, a sheet and the player, and back to where it was', async ({ page }) => {
  await signIn(page)
  await page.goto('/sports')
  const first = listing(page, 'Riverton Rovers vs Harbor City'), second = listing(page, 'Northfield at Lakeside')
  await expect(first).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowRight')
  await expect(second).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  const feeds = page.getByTestId('sports-feed')
  await expect(feeds.nth(0)).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowDown')
  await expect(feeds.nth(1)).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowDown')
  await expect(page.getByTestId('sports-picker').getByRole('button', { name: 'Close' })).toHaveAttribute('data-focused', 'true')
  // The picker keeps the remote inside it.
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('ArrowRight')
  await expect(page.getByTestId('sports-picker').locator('.fx[data-focused="true"]')).toHaveCount(1)
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('sports-picker')).toHaveCount(0)
  await expect(second).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  await expect(feeds.nth(0)).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Backspace')
  await expect(page.getByTestId('sports-picker')).toHaveCount(0)
  await expect(second).toHaveAttribute('data-focused', 'true')
  // Down into what is coming: a sheet, and Back returns to the row that opened it.
  for (let step = 0; step < 4 && await focused(page).getAttribute('data-kind') !== 'upcoming'; step++) await page.keyboard.press('ArrowDown')
  const row = await focused(page).getAttribute('aria-label')
  expect(row).toMatch(/, (Today|Tomorrow) at /)
  await page.keyboard.press('Enter')
  const sheet = page.getByTestId('sports-sheet')
  await expect(sheet.locator('.fx[data-focused="true"]')).toHaveCount(1)
  await page.keyboard.press('ArrowDown')
  await expect(sheet.locator('.fx[data-focused="true"]')).toHaveCount(1)
  await page.keyboard.press('Escape')
  await expect(sheet).toHaveCount(0)
  await expect(focused(page)).toHaveAttribute('aria-label', row!)
  // Up to the live games and into the player with Enter; Back comes home to the same card.
  for (let step = 0; step < 4 && await focused(page).getAttribute('data-kind') !== 'live'; step++) await page.keyboard.press('ArrowUp')
  for (let step = 0; step < 3 && await first.getAttribute('data-focused') !== 'true'; step++) await page.keyboard.press('ArrowLeft')
  await expect(first).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/play\/ch%3As_xt%3A9004$/)
  await expect(page.getByTestId('player')).toBeVisible()
  for (let step = 0; step < 3 && /\/play\//.test(page.url()); step++) { await page.keyboard.press('Escape'); await page.waitForTimeout(400) }
  await expect(page).toHaveURL(/\/sports$/)
  await expect(first).toHaveAttribute('data-focused', 'true')
})

test('the owner records a coming game or every game of a side from its sheet', async ({ page }) => {
  await asOwner(page)
  await page.goto('/sports')
  const row = listing(page, 'Oakdale at Port Linden')
  await row.click()
  const sheet = page.getByTestId('sports-sheet')
  await expect(sheet.getByRole('heading')).toHaveText('Oakdale at Port Linden')
  await expect(sheet).toContainText(/(Today|Tomorrow) · \d{1,2}:\d{2}/)
  await expect(sheet).toContainText("Men's Soccer · VANTIX+ 003 · Ch 903")
  await expect(sheet.getByRole('button')).toHaveText(['Record this game', 'Record every Oakdale game', 'Record every Port Linden game', 'Close'])
  await expect(sheet.getByTestId('sports-record')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('status').filter({ hasText: 'Oakdale at Port Linden will be recorded at' })).toBeVisible()
  await expect(sheet).toHaveCount(0)
  await expect(row).toHaveAttribute('data-focused', 'true')
  await expect(row.getByTestId('recording-badge')).toHaveText('Will record')
  await page.keyboard.press('Enter')
  await expect(sheet.getByTestId('recording-badge')).toHaveText('Will record')
  await expect(sheet.getByTestId('sports-record')).toHaveCount(0)
  await sheet.getByTestId('sports-record-cancel').click()
  await expect(row.getByTestId('recording-badge')).toHaveCount(0)
  await row.click()
  await sheet.getByRole('button', { name: 'Record every game for Port Linden', exact: true }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Every game for Port Linden will be recorded.' })).toBeVisible()
  await expect(sheet).toHaveCount(0)
})

const LONG = 'Northfield University Academicals Second Eleven at Lakeside Collegiate Institute Reserves'
const UNBROKEN = '(XX)(NETWORK+008)|FieldHockey:NorthfieldUniversityAcademicalsAtLakesideCollegiateInstitute(2026-10-0214:50:00)'
const WIDE: StubEvent[] = [
  { id: 'long', title: LONG, sport: "Women's Field Hockey Championship Qualifier", startMin: -30, feeds: [{ id: 'L1', name: `${UNBROKEN} ONE`, number: 10801, quality: 'UHD', language: 'eng' }, { id: 'L2', name: 'VANTIX+ INTERNATIONAL EXTRA 104', number: 10802, quality: 'FHD', language: 'spa' }] },
  { id: 'unbroken', title: UNBROKEN, sport: 'Field Hockey', startMin: -20, feeds: [{ id: 'U1', name: UNBROKEN, number: 10803, quality: 'HD' }] },
  { id: 'later', title: LONG, sport: "Women's Field Hockey Championship Qualifier", startMin: 90, home: 'Lakeside Collegiate Institute Reserves', away: 'Northfield University Academicals Second Eleven', feeds: [{ id: 'L3', name: UNBROKEN, number: 10804 }] },
  { id: 'later2', title: UNBROKEN, sport: 'Field Hockey', startMin: 100, feeds: [{ id: 'U2', name: 'VANTIX+ 009', number: 10805 }] },
  { id: 'chan', kind: 'channel', title: LONG, sport: 'Sports', startMin: 0, league: 'CORVAN LEAGUE', feeds: [{ id: 'G1', name: `CORVAN LEAGUE ${UNBROKEN}`, number: 10806, quality: '4K' }] },
]

for (const [width, height] of [[390, 844], [768, 1024], [1280, 720], [1920, 1080]] as const) {
  test(`nothing scrolls sideways at ${width}px, however long a name is`, async ({ page }) => {
    await stubSports(page, WIDE, [{ id: 'N1', name: UNBROKEN, number: 10901, quality: 'HD', now: LONG }, { id: 'N2', name: LONG, number: 10902, quality: '4K' }])
    await asOwner(page)
    await page.setViewportSize({ width, height })
    await page.goto('/sports')
    await expect(page.getByTestId('sports-listing')).toHaveCount(4)
    await expect(page.getByTestId('sports-channel')).toHaveCount(3)
    expect(await fits(page)).toBe(true)
    // No card, row or badge is wider than the screen's own content box; a row of tiles scrolls inside its own box.
    const widest = await page.evaluate(() => Math.max(...Array.from(document.querySelectorAll('[data-testid=sports] *')).filter((el) => !el.parentElement?.closest('.row-scroll')).map((el) => el.getBoundingClientRect().right)))
    expect(widest).toBeLessThanOrEqual(width)
    await page.getByTestId('sports-listing').nth(0).click()
    const picker = page.getByTestId('sports-picker')
    await expect(picker.getByTestId('sports-feed')).toHaveCount(2)
    expect(await fits(page)).toBe(true)
    const inside = async (testId: string) => page.getByTestId(testId).evaluate((dialog) => { const box = dialog.getBoundingClientRect(); return Array.from(dialog.querySelectorAll('*')).every((el) => { const r = el.getBoundingClientRect(); return r.left >= box.left - 1 && r.right <= box.right + 1 }) })
    expect(await inside('sports-picker')).toBe(true)
    expect((await picker.boundingBox())!.width).toBeLessThanOrEqual(width)
    await page.keyboard.press('Escape')
    await page.locator('[data-testid=sports-listing][data-kind=upcoming]').filter({ hasText: 'Lakeside Collegiate' }).click()
    const sheet = page.getByTestId('sports-sheet')
    await expect(sheet.getByTestId('sports-record-team')).toHaveCount(2)
    expect(await fits(page)).toBe(true)
    expect(await inside('sports-sheet')).toBe(true)
    await page.keyboard.press('Escape')
    await page.getByTestId('sports-filters').click()
    await page.getByLabel('Search games, teams or channels').fill(UNBROKEN)
    expect(await fits(page)).toBe(true)
    await page.getByRole('button', { name: 'Done', exact: true }).click()
    await expect(page.getByTestId('sports-filter-summary')).toBeVisible()
    expect(await fits(page)).toBe(true)
  })
}

test('a channel without a guide is shown by its name alone, in Live TV and on Home', async ({ page }) => {
  // What the server sends for a channel whose only "programme" was a placeholder: no programme.
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const request = new Request(input, init), url = new URL(request.url)
      if (url.pathname !== '/api/v1/home' && url.pathname !== '/api/v1/channels') return original(request)
      const body = await (await original(request)).json()
      if (url.pathname === '/api/v1/channels') body.channels[0] = { ...body.channels[0], name: 'COMBAT HD', now: null, next: null }
      else { const row = body.rows.find((r: { id: string }) => r.id === 'onnow'); row.tiles[0] = { ...row.tiles[0], title: 'COMBAT HD', now_playing: null } }
      return Response.json(body)
    }
  })
  await signIn(page)
  const tile = page.getByRole('link', { name: 'COMBAT HD', exact: true })
  await expect(tile).toBeVisible()
  expect((await tile.innerText()).match(/COMBAT HD/g)).toHaveLength(1)
  await expect(tile).toContainText('No programme data')
  await page.goto('/live')
  const row = page.getByTestId('channel-row').first()
  await expect(row).toContainText('COMBAT HD')
  expect((await row.innerText()).match(/COMBAT HD/g)).toHaveLength(1)
})
