import { expect, test, type Locator, type Page } from '@playwright/test'
import { focusBarEntry, focusedLabel, searchBox } from './helpers'

// One search: a small box in the top bar (the Search tab's page on a phone), and one page of results
// with up to three sections: On TV, Channels, Movies & Shows. Fixture queries: "harbor" finds all three,
// "sunday league" all three with a channel named exactly that, "metro" a channel only, "velvet" a title
// no source has, "zzzz" nothing.
const profile = (id: string, name: string, is_admin = false) => ({ id, name, avatar: null, is_admin, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' })
const WHO = { owner: profile('p1', 'Owner', true), guest: profile('p2', 'Guest'), kids: profile('p3', 'Kids') }
const PHONE = { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true }

/** Start signed in, past the first-run notice. */
async function enter(page: Page, path = '/', who: keyof typeof WHO = 'guest') {
  await page.addInitScript((profile) => {
    sessionStorage.setItem('mock.accepted', JSON.stringify(['p1', 'p2', 'p3']))
    if (!localStorage.getItem('sh.session.v1')) localStorage.setItem('sh.session.v1', JSON.stringify({ access: `mock-access-${profile.id}`, refresh: `mock-refresh-${profile.id}`, profile }))
  }, WHO[who])
  await page.goto(path)
}
const results = (page: Page) => page.getByTestId('search-results')
const sections = (page: Page) => results(page).locator('section')
const sectionNames = (page: Page) => sections(page).evaluateAll((all) => all.map((s) => s.getAttribute('aria-label')))
const fits = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
const rect = (target: Locator) => target.evaluate((e) => e.getBoundingClientRect().toJSON() as DOMRect)
const focused = (page: Page) => page.locator('.fx[data-focused="true"]').last()
const searches = (page: Page) => page.evaluate(() => JSON.parse(sessionStorage.getItem('test.searches') ?? '[]') as string[])
/** Note every search the page sends, to count them. */
const recordSearches = (page: Page) => page.addInitScript(() => {
  const original = window.fetch.bind(window)
  window.fetch = async (input, init) => {
    const request = new Request(input, init), url = new URL(request.url)
    if (url.pathname === '/api/v1/search') sessionStorage.setItem('test.searches', JSON.stringify([...(JSON.parse(sessionStorage.getItem('test.searches') ?? '[]') as string[]), url.search]))
    return original(request)
  }
})

test('the top bar has one search control; typing in it shows results and keeps the caret', async ({ page }) => {
  await recordSearches(page)
  await enter(page)
  await expect(page.getByTestId('hero-play')).toHaveAttribute('data-focused', 'true')
  const bar = page.getByTestId('rail')
  await expect(bar.getByRole('textbox')).toHaveCount(1)
  await expect(page.getByRole('textbox')).toHaveCount(1)
  await expect(bar.getByRole('link', { name: 'Search', exact: true })).toHaveCount(0)
  const box = searchBox(page)
  // A short field beside the magnifier, at the far end before More and the profile.
  const field = await rect(page.getByTestId('nav-search'))
  const rem = await page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).fontSize))
  expect(Math.abs(field.width - 14 * rem)).toBeLessThan(2)
  expect(field.x).toBeGreaterThan((await rect(page.getByTestId('nav-my-list'))).x)
  expect(field.x + field.width).toBeLessThanOrEqual((await rect(page.getByTestId('nav-more'))).x)
  await expect(page.getByTestId('nav-search').locator('svg')).toBeVisible()

  // Clicking it goes nowhere: it only takes the caret, and may widen without moving the names beside it.
  const names = await rect(page.getByTestId('nav-my-list'))
  await box.click()
  await expect(box).toBeFocused()
  await expect(page).toHaveURL(/\/$/)
  await expect.poll(async () => (await rect(page.getByTestId('nav-search'))).width).toBeGreaterThan(field.width)
  expect((await rect(page.getByTestId('nav-my-list'))).x).toBe(names.x)
  expect(await fits(page)).toBe(true)

  // Typing asks once the viewer pauses, not on every letter, and the caret stays in the box.
  await page.keyboard.type('harbor', { delay: 40 })
  await expect(page).toHaveURL(/\/search\?q=harbor$/)
  await expect(page.getByTestId('search-titles').getByTestId('tile')).toHaveAttribute('aria-label', 'Harbor Lights')
  await expect(box).toBeFocused()
  await expect(box).toHaveValue('harbor')
  // Only the finished word was asked for: once for what the hub holds, once for what the provider adds
  // (the development build mounts twice, so each may be seen twice here).
  expect((await searches(page)).every((query) => query.includes('q=harbor&'))).toBe(true)
  expect((await searches(page)).length).toBeLessThanOrEqual(4)
  await expect(page.getByTestId('nav-search')).toHaveAttribute('aria-current', 'page')

  // More letters replace the entry: one Back leaves search for where the viewer was.
  await page.keyboard.type(' li')
  await expect(page).toHaveURL(/\/search\?q=harbor%20li$/)
  await expect(page.getByTestId('search-titles').getByTestId('tile')).toHaveCount(1)
  await page.goBack()
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByTestId('home')).toBeVisible()
  await expect(searchBox(page)).toHaveValue('')
})

test('the results page has no filters, chips or notes: sections of results and nothing else', async ({ page }) => {
  await enter(page, '/search?q=harbor')
  await expect(page.getByTestId('search-titles')).toBeVisible()
  // The box shows the query of the page.
  await expect(searchBox(page)).toHaveValue('harbor')
  const screen = page.getByTestId('search')
  await expect(screen.getByRole('textbox')).toHaveCount(0)
  await expect(screen.locator('.chip')).toHaveCount(0)
  await expect(screen.getByRole('button', { name: /language|filter|sort|source|room|popular/i })).toHaveCount(0)
  await expect(screen.getByRole('heading', { level: 1 })).toHaveCount(0)
  // The only buttons are "Show more"; every other control is a result.
  for (const button of await screen.getByRole('button').all()) await expect(button).toHaveText('Show more')
  await expect(screen.getByText(/coverage|metadata|not in your library|type at least/i)).toHaveCount(0)
  // Results start right under the bar.
  const bar = await rect(page.getByTestId('rail'))
  const first = await rect(sections(page).first())
  expect(first.y - bar.height).toBeLessThan(40)
  expect(await fits(page)).toBe(true)
})

test('three sections in order: On TV, Channels, Movies & Shows; a channel named exactly as the query leads', async ({ page }) => {
  await enter(page, '/search?q=harbor')
  await expect(page.getByTestId('search-titles')).toBeVisible()
  expect(await sectionNames(page)).toEqual(['On TV', 'Channels', 'Movies & Shows'])
  await expect(sections(page).locator('h2')).toHaveText(['On TV', 'Channels', 'Movies & Shows'])
  // A broadcast says its title, when it is on and where; a channel its number, name and what is on.
  const broadcast = page.getByTestId('search-broadcast').first()
  await expect(broadcast).toContainText('Riverton Rovers vs Harbor City')
  await expect(broadcast).toContainText(/On now|Today|Tomorrow|Mon|Tue|Wed|Thu|Fri|Sat|Sun/)
  await expect(broadcast).toContainText(/\d+ · \w+/)
  const channel = page.getByTestId('search-channel')
  await expect(channel).toHaveCount(1)
  await expect(channel).toHaveAttribute('aria-label', /^1 Harbor News 24, now /)
  // An owned title carries its library mark.
  await expect(page.getByTestId('search-titles').getByTestId('tile').getByTestId('tile-available')).toBeVisible()

  await searchBox(page).fill('sunday league')
  await expect(page.getByTestId('search-titles')).toContainText('Sunday League Legends')
  expect(await sectionNames(page)).toEqual(['Channels', 'On TV', 'Movies & Shows'])
  await expect(page.getByTestId('search-channel')).toHaveAttribute('aria-label', /^14 Sunday League/)
  await expect(page.getByTestId('search-broadcast')).toContainText('Sunday League Live')
  await expect(page.getByTestId('search-broadcast')).toContainText(/(Tomorrow|Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d+:00/)

  // A section with nothing in it is not shown at all.
  await searchBox(page).fill('metro')
  await expect.poll(() => sectionNames(page)).toEqual(['Channels'])
  await expect(page.getByTestId('search-channel')).toHaveAttribute('aria-label', /^2 Metro Sports 1/)
  await searchBox(page).fill('velvet')
  await expect.poll(() => sectionNames(page)).toEqual(['Movies & Shows'])
})

test('a broadcast that is on now plays its channel; so does a channel', async ({ page }) => {
  // "the" is in enough listings that one of them is always on.
  await enter(page, '/search?q=the')
  const live = page.locator('[data-testid="search-broadcast"][data-live="true"]').first()
  await expect(live).toBeVisible()
  await expect(live).toContainText('On now')
  // On-now broadcasts come before the ones still to start.
  await expect(page.getByTestId('search-broadcast').first()).toHaveAttribute('data-live', 'true')
  const label = (await live.getAttribute('aria-label'))!
  await live.click()
  await expect(page).toHaveURL(/\/play\/ch%3A/)
  await expect(page.getByTestId('player')).toBeVisible()
  expect(label).toMatch(/, on now, channel \d+ /)
  await page.goBack()
  await expect(page).toHaveURL(/\/search\?q=the$/)

  await searchBox(page).fill('metro')
  await expect(page.getByTestId('search-channel')).toHaveCount(1)
  await expect(page.getByTestId('search-channel')).toHaveAttribute('aria-label', /^2 Metro Sports 1/)
  await page.getByTestId('search-channel').click()
  await expect(page).toHaveURL(/\/play\/ch%3As_xt%3A1002$/)
  await expect(page.getByTestId('player')).toBeVisible()
})

test('a broadcast still to come opens the record sheet for who may record, and says when it starts for who may not', async ({ browser }) => {
  for (const who of ['owner', 'kids'] as const) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 720 } })
    const page = await context.newPage()
    await enter(page, '/search?q=sunday%20league%20live', who)
    const upcoming = page.getByTestId('search-broadcast')
    await expect(upcoming).toHaveAttribute('data-live', 'false')
    await upcoming.click()
    await expect(page).toHaveURL(/\/search\?q=/)
    if (who === 'owner') {
      await expect(page.getByTestId('record-dialog')).toContainText('Sunday League Live')
      await page.getByTestId('record-one').click()
      await expect(page.getByTestId('record-dialog')).toHaveCount(0)
      await expect(upcoming.getByTestId('recording-badge')).toBeVisible()
    } else {
      await expect(page.getByTestId('record-dialog')).toHaveCount(0)
      await expect(page.getByRole('status').filter({ hasText: /Sunday League Live starts (Tomorrow|Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d+:00.* on 2 Metro Sports 1/ })).toBeVisible()
    }
    await context.close()
  }
})

test('a title that can be requested says so on its tile, and shows its request once asked for', async ({ page }) => {
  await enter(page, '/search?q=velvet')
  const tile = page.getByTestId('search-titles').getByTestId('tile')
  await expect(tile).toHaveAttribute('aria-label', 'Velvet Antarctica')
  // A title that does not play here carries no mark in search; one that does carries the tick.
  await expect(tile.getByTestId('tile-available')).toHaveCount(0)
  await expect(tile.locator('.tile-badges')).toHaveCount(0)
  // An admin connects a request manager and the viewer asks for the title, as on its page.
  const api = (who: string, method: string, path: string, body: unknown) => page.evaluate(async ({ who, method, path, body }) => {
    const res = await fetch(`/api/v1${path}`, { method, headers: { authorization: `Bearer mock-access-${who}`, 'content-type': 'application/json' }, body: JSON.stringify(body) })
    return res.status
  }, { who, method, path, body })
  expect(await api('p1', 'PUT', '/settings/request_manager', { url: 'http://requests.example.test:5055', api_key: 'fixture-manager-key' })).toBe(200)
  expect(await api('p2', 'POST', '/requests', { media_type: 'movie', tmdb_id: 301 })).toBeLessThan(300)
  await page.reload()
  await expect(tile).toContainText(/Requested|Approved|Searching/)
  // Its page is where it is asked for and followed.
  await tile.click()
  await expect(page).toHaveURL(/\/title\/tmdb%3Amovie%3A301$/)
  await expect(page.getByTestId('title-request-state')).toBeVisible()
})

test('what the hub holds is on screen before the metadata provider answers, and nothing moves when it does', async ({ page }) => {
  await recordSearches(page)
  // The provider is slow: every search that asks it takes a second and a half longer.
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const request = new Request(input, init), url = new URL(request.url)
      if (url.pathname === '/api/v1/search' && url.searchParams.get('discover') !== 'false') await new Promise((done) => setTimeout(done, 1500))
      return original(request)
    }
  })
  await enter(page, '/search?q=the')
  const tiles = page.getByTestId('search-titles').getByTestId('tile')
  // The library's three titles, the channel and the broadcasts are there while the provider is still asked.
  await expect(tiles).toHaveCount(3)
  await expect(page.getByTestId('search-broadcast').first()).toBeVisible()
  await expect(page.getByTestId('search-channel')).toHaveCount(1)
  const waiting = page.getByTestId('search-discovering')
  await expect(waiting).toBeVisible()
  await expect(results(page)).not.toHaveAttribute('data-stale', 'true')
  const names = await tiles.evaluateAll((all) => all.map((tile) => tile.getAttribute('aria-label')))
  expect(names).toEqual(['The Quiet Meridian', 'The Understudy', 'The Understudies'])
  const place = async () => Promise.all([...(await tiles.all()).slice(0, 3), page.getByTestId('search-broadcast').first(), page.getByTestId('search-channel'), sections(page).last().locator('h2')].map(async (item) => { const r = await rect(item); return [r.x, r.y, r.width, r.height].map(Math.round) }))
  // Focus on a result while waiting: it stays where it is, too.
  await tiles.nth(1).hover()
  await expect(tiles.nth(1)).toHaveAttribute('data-focused', 'true')
  await page.waitForTimeout(400) // the focus ring's own transition
  const before = await place()
  const slot = await rect(waiting)
  const scrolled = await page.evaluate(() => window.scrollY)

  // The provider's answer adds the title no source has, after the library's, where the placeholder was.
  await expect(tiles).toHaveCount(4, { timeout: 8000 })
  await expect(waiting).toHaveCount(0)
  expect(await tiles.evaluateAll((all) => all.map((tile) => tile.getAttribute('aria-label')))).toEqual([...names, 'The Tollgate Tapes'])
  expect(await place()).toEqual(before)
  const added = await rect(tiles.nth(3))
  expect([Math.round(added.x), Math.round(added.y)]).toEqual([Math.round(slot.x), Math.round(slot.y)])
  await expect(tiles.nth(1)).toHaveAttribute('data-focused', 'true')
  expect(await page.evaluate(() => window.scrollY)).toBe(scrolled)
  const asked = await searches(page)
  expect(asked.some((query) => query.includes('discover=false'))).toBe(true)
  expect(asked.some((query) => !query.includes('discover=false') && query.includes('kinds=movie'))).toBe(true)

  // A provider answer for a query the viewer has moved on from is dropped: "the" never shows under "velvet".
  await page.goto('/search?q=the%20under')
  await expect(tiles).toHaveCount(2)
  await searchBox(page).fill('velvet')
  await expect(page.getByTestId('search-titles').getByRole('link', { name: 'Velvet Antarctica', exact: true })).toBeVisible({ timeout: 8000 })
  await expect(tiles).toHaveCount(1)
  await page.waitForTimeout(1800)
  await expect(tiles).toHaveCount(1)
  await expect(page.getByText('The Understudy')).toHaveCount(0)
})

test('Movie or Series is said only on results that share a name; an older server answering titles alone still shows them', async ({ page }) => {
  await page.addInitScript(() => {
    const source = { source_id: 'lib', source_type: 'plex', name: 'Library', playable_in_app: true, kind: 'library' }
    const tile = (id: string, kind: string, title: string, year: number) => ({ id, kind, title, year, sources: [source], is_favorite: false })
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const request = new Request(input, init)
      if (new URL(request.url).pathname !== '/api/v1/search') return original(request)
      // The answer of a server from before the unified search: no programmes, sections or order.
      return Response.json({ results: [tile('tmdb:movie:71', 'movie', 'Harbour Lights', 1987), tile('tmdb:tv:72', 'show', 'Harbour Lights', 2005), tile('tmdb:movie:73', 'movie', 'Harbour Lights Forever', 2021)], play_here: [], open_in_app: [], channels: [], attribution: 'Availability data by JustWatch' })
    }
  })
  await enter(page, '/search?q=harbour%20lights')
  const tiles = page.getByTestId('search-titles').locator('.search-tile')
  await expect(tiles).toHaveCount(3)
  expect(await sectionNames(page)).toEqual(['Movies & Shows'])
  await expect(tiles.nth(0).getByTestId('search-kind')).toHaveText('Movie')
  await expect(tiles.nth(1).getByTestId('search-kind')).toHaveText('Series')
  await expect(tiles.nth(2).getByTestId('search-kind')).toHaveCount(0)
  // Title and year are on every poster.
  await expect(tiles.nth(1)).toContainText('Harbour Lights (2005)')
  await expect(page.getByTestId('search-more-titles')).toHaveCount(0)
})

test('Movies and Shows have no search field of their own, and their header closes up', async ({ page }) => {
  for (const section of ['movies', 'shows']) {
    await enter(page, `/browse/${section}`)
    const browse = page.getByTestId('browse')
    await expect(page.getByTestId('row').first()).toBeVisible()
    await expect(browse.getByRole('textbox')).toHaveCount(0)
    await expect(browse.getByText(/search/i)).toHaveCount(0)
    // The title and Filter, side by side, and the first shelf right under them.
    const heading = await rect(browse.getByRole('heading', { level: 1 })), filter = await rect(page.getByTestId('browse-filter'))
    expect(Math.abs(heading.y + heading.height / 2 - (filter.y + filter.height / 2))).toBeLessThan(6)
    expect(filter.x - (heading.x + heading.width)).toBeLessThan(40)
    expect((await rect(page.getByTestId('row').first())).y - (filter.y + filter.height)).toBeLessThan(40)
  }
  // Nor does the metadata catalog.
  await page.goto('/discover/movies')
  await expect(page.getByTestId('discover')).toBeVisible()
  await expect(page.getByTestId('discover').getByRole('button', { name: /search/i })).toHaveCount(0)
  await expect(page.getByRole('textbox', { name: /search/i })).toHaveCount(1)
})

test('"/" puts the caret in the box from anywhere but another field or an open dialog', async ({ page }) => {
  await enter(page)
  await expect(page.getByTestId('hero-play')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('/')
  await expect(searchBox(page)).toBeFocused()
  await expect(searchBox(page)).toHaveValue('')
  // In the box "/" is a character like any other.
  await page.keyboard.type('a/b')
  await expect(searchBox(page)).toHaveValue('a/b')
  // In another field it types; the box is not involved.
  await page.goto('/discover/movies')
  const year = page.getByLabel('Release year', { exact: true })
  await year.click()
  await page.keyboard.press('/')
  await expect(year).toBeFocused()
  // Nor under an open dialog.
  await page.goto('/')
  await expect(page.getByTestId('hero-play')).toHaveAttribute('data-focused', 'true')
  await page.getByTestId('nav-more').click()
  await expect(page.getByTestId('more-menu')).toBeVisible()
  await page.keyboard.press('/')
  await expect(searchBox(page)).not.toBeFocused()
  await expect(page.getByTestId('more-menu')).toBeVisible()
})

test('Escape clears the box, then leaves search; in an empty box away from search it returns to the screen', async ({ page }) => {
  await enter(page)
  await expect(page.getByTestId('hero-play')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('/')
  await page.keyboard.type('harbor')
  await expect(page.getByTestId('search-titles')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(searchBox(page)).toHaveValue('')
  await expect(searchBox(page)).toBeFocused()
  await expect(page).toHaveURL(/\/search$/)
  await expect(page.getByTestId('search-titles')).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByTestId('home')).toBeVisible()
  await expect(searchBox(page)).not.toBeFocused()
  await expect(page.getByTestId('hero-play')).toHaveAttribute('data-focused', 'true')
  // Nothing typed, not on search: Escape just gives the screen its focus back.
  await page.keyboard.press('/')
  await expect(searchBox(page)).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(searchBox(page)).not.toBeFocused()
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByTestId('hero-play')).toHaveAttribute('data-focused', 'true')
  // The cross in the box clears it too, and leaves the caret there.
  await searchBox(page).fill('metro')
  await expect(page.getByTestId('search-channel')).toBeVisible()
  await page.getByRole('button', { name: 'Clear search', exact: true }).click()
  await expect(searchBox(page)).toHaveValue('')
  await expect(searchBox(page)).toBeFocused()
})

test('arrows: Down from the box into the first result, through every section, Up back to the box; Back from a title restores results, focus and scroll', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 500 })
  await enter(page)
  await expect(page.getByTestId('hero-play')).toHaveAttribute('data-focused', 'true')
  await focusBarEntry(page, 'nav-search')
  await page.keyboard.press('Enter')
  await page.keyboard.type('harbor')
  await expect(page.getByTestId('search-titles')).toBeVisible()
  await page.keyboard.press('ArrowDown')
  await expect(page.getByTestId('search-broadcast').first()).toHaveAttribute('data-focused', 'true')
  await expect(searchBox(page)).not.toBeFocused()
  // Up returns to the box, as a stop: the caret is the viewer's to ask for.
  await page.keyboard.press('ArrowUp')
  await expect(page.getByTestId('nav-search')).toHaveAttribute('data-focused', 'true')
  await expect(searchBox(page)).not.toBeFocused()
  await page.keyboard.press('ArrowDown')
  await expect(page.getByTestId('search-broadcast').first()).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowRight')
  await expect(page.getByTestId('search-broadcast').nth(1)).toHaveAttribute('data-focused', 'true')
  // Down the page: the rest of On TV, its Show more, the channel, then the poster.
  const seen: string[] = []
  for (let i = 0; i < 8 && !(await page.getByTestId('search-titles').locator('.fx[data-focused="true"]').count()); i++) {
    await page.keyboard.press('ArrowDown')
    seen.push((await focused(page).getAttribute('data-testid')) ?? '')
  }
  expect(seen).toContain('search-more-programmes')
  expect(seen).toContain('search-channel')
  expect(seen.at(-1)).toBe('tile')
  expect(await focusedLabel(page)).toBe('Harbor Lights')
  const scrolled = await page.evaluate(() => window.scrollY)
  expect(scrolled).toBeGreaterThan(0)
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/title\/tmdb%3Amovie%3A101$/)
  await expect(searchBox(page)).toHaveValue('')
  // Backspace is Back, like Escape.
  await page.keyboard.press('Backspace')
  await expect(page).toHaveURL(/\/search\?q=harbor$/)
  await expect(searchBox(page)).toHaveValue('harbor')
  await expect(page.getByTestId('search-titles').getByTestId('tile')).toHaveAttribute('data-focused', 'true')
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(scrolled)
  await expect(page.locator('.fx[data-focused="true"]')).toHaveCount(1)
})

test('Tab walks the bar into the results in page order, and types in the box when it lands there', async ({ page }) => {
  await enter(page, '/search?q=sunday%20league')
  await expect(page.getByTestId('search-titles')).toBeVisible()
  await expect(page.getByTestId('search-channel')).toHaveAttribute('data-focused', 'true')
  const stops: string[] = []
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press('Tab')
    stops.push((await focused(page).getAttribute('data-testid')) ?? '')
  }
  expect(stops).toEqual(['search-broadcast', 'tile', 'nav-home', 'nav-live-tv', 'nav-movies', 'nav-shows', 'nav-my-list', 'nav-search', 'nav-more', 'nav-profile', 'search-channel', 'search-broadcast'])
  // Shift+Tab back to the box: it takes the caret, so the next keys are text.
  for (let i = 0; i < 4; i++) await page.keyboard.press('Shift+Tab')
  await expect(page.getByTestId('nav-search')).toHaveAttribute('data-focused', 'true')
  await expect(searchBox(page)).toBeFocused()
  await page.keyboard.press('Control+a')
  await page.keyboard.type('metro')
  await expect(page.getByTestId('search-channel')).toHaveAttribute('aria-label', /Metro Sports 1/)
  await page.keyboard.press('Tab')
  await expect(page.getByTestId('nav-more')).toHaveAttribute('data-focused', 'true')
  await expect(searchBox(page)).not.toBeFocused()
})

test('Show more extends a section in place and moves focus to the first row it added', async ({ page }) => {
  await recordSearches(page)
  await enter(page, '/search?q=the')
  const rows = page.getByTestId('search-broadcast')
  await expect(rows).toHaveCount(6)
  const first = await rows.first().getAttribute('aria-label')
  await page.evaluate(() => window.scrollTo(0, 120))
  const more = page.getByTestId('search-more-programmes')
  await more.click()
  await expect.poll(() => rows.count()).toBeGreaterThan(24)
  expect(await rows.first().getAttribute('aria-label')).toBe(first)
  await expect(rows.nth(6)).toHaveAttribute('data-focused', 'true')
  await expect(more).toHaveCount(0)
  // The page did not jump to the top to do it.
  expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(0)
  expect((await searches(page)).some((query) => query.includes('expand=programmes'))).toBe(true)
  // The other sections are as they were.
  await expect(page.getByTestId('search-titles')).toBeVisible()
  // A new query starts at the top again, on its first few rows.
  await searchBox(page).fill('harbor')
  await expect(page.getByTestId('search-titles').getByTestId('tile')).toHaveCount(1)
  await expect(page.getByTestId('search-titles').getByTestId('tile')).toHaveAttribute('aria-label', 'Harbor Lights')
  await expect(rows).toHaveCount(6)
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0)
})

test('nothing found says so with a hint; loading shows skeletons in the page, not a blank screen', async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const request = new Request(input, init)
      if (new URL(request.url).pathname === '/api/v1/search') await new Promise((done) => setTimeout(done, 900))
      return original(request)
    }
  })
  await enter(page, '/search?q=zzzz')
  await expect(page.getByTestId('search-loading')).toBeVisible()
  await expect(page.getByTestId('rail')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'No results for “zzzz”', exact: true })).toBeVisible()
  await expect(page.getByTestId('search').getByText('Check the spelling, or try fewer words.')).toBeVisible()
  await expect(page.getByTestId('search-loading')).toHaveCount(0)
  await expect(sections(page)).toHaveCount(0)
  await expect(page.getByTestId('search').getByRole('button')).toHaveCount(0)
  // While the next query loads, the answer on screen stays up instead of flashing empty.
  await searchBox(page).fill('harbor')
  await expect(page.getByTestId('search-titles')).toBeVisible()
  await searchBox(page).fill('metro')
  await expect(results(page)).toHaveAttribute('data-stale', 'true')
  await expect(page.getByTestId('search-titles')).toBeVisible()
  await expect.poll(() => sectionNames(page)).toEqual(['Channels'])
  await expect(results(page)).not.toHaveAttribute('data-stale', 'true')
})

test('a narrow bar shows the magnifier alone; the box opens over the bar while it is in use', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 700 })
  await enter(page)
  await expect(page.getByTestId('hero-play')).toHaveAttribute('data-focused', 'true')
  const control = page.getByTestId('nav-search')
  const idle = await rect(control)
  expect(idle.width).toBeLessThan(48)
  await expect(page.getByTestId('nav-my-list')).toBeVisible()
  expect(await fits(page)).toBe(true)
  await control.click()
  await expect(searchBox(page)).toBeFocused()
  const open = await rect(control)
  expect(open.width).toBeGreaterThan(700)
  expect(open.x + open.width).toBeLessThanOrEqual(900)
  expect(open.y + open.height).toBeLessThanOrEqual((await rect(page.getByTestId('rail'))).height)
  expect(await fits(page)).toBe(true)
  await page.keyboard.type('harbor')
  await expect(page.getByTestId('search-titles')).toBeVisible()
  await expect(searchBox(page)).toBeFocused()
  // Down into the results closes it again: the names are back.
  await page.keyboard.press('ArrowDown')
  await expect(page.getByTestId('search-broadcast').first()).toHaveAttribute('data-focused', 'true')
  expect((await rect(control)).width).toBeLessThan(48)
  await expect(page.getByTestId('nav-my-list')).toBeVisible()
  // From 1000 px the field is always there.
  await page.setViewportSize({ width: 1001, height: 700 })
  expect((await rect(control)).width).toBeGreaterThan(120)
  await expect(searchBox(page)).toHaveValue('harbor')
  expect(await fits(page)).toBe(true)
})

test.describe('on a phone', () => {
  test.use(PHONE)

  test('the Search tab opens the same results page with the box at its top; nothing scrolls sideways', async ({ page }) => {
    await enter(page)
    await expect(page.getByTestId('home')).toBeVisible()
    await expect(page.getByRole('textbox')).toHaveCount(0)
    await page.getByTestId('nav-search').tap()
    await expect(page).toHaveURL(/\/search$/)
    const box = searchBox(page)
    await expect(box).toBeFocused()
    await expect(page.getByRole('textbox')).toHaveCount(1)
    await expect(page.getByTestId('search-idle')).toBeVisible()
    // The box is the first thing on the page.
    expect((await rect(page.getByRole('search'))).y).toBeLessThan(40)
    expect(await fits(page)).toBe(true)
    await box.fill('harbor')
    await expect(page).toHaveURL(/\/search\?q=harbor$/)
    await expect(page.getByTestId('search-titles')).toBeVisible()
    await expect(box).toBeFocused()
    expect(await sectionNames(page)).toEqual(['On TV', 'Channels', 'Movies & Shows'])
    // A phone opens the live sections on three rows each.
    await expect(page.getByTestId('search-broadcast')).toHaveCount(3)
    expect(await fits(page)).toBe(true)
    for (const row of await page.locator('[data-testid="search-broadcast"], [data-testid="search-channel"], [data-testid="search-titles"] [data-testid="tile"]').all()) {
      const r = await rect(row)
      expect(r.x).toBeGreaterThanOrEqual(0)
      expect(r.x + r.width).toBeLessThanOrEqual(390)
    }
    await expect(page.getByTestId('search').locator('.chip')).toHaveCount(0)
    await page.getByTestId('search-more-programmes').tap()
    await expect.poll(() => page.getByTestId('search-broadcast').count()).toBeGreaterThan(3)
    expect(await fits(page)).toBe(true)

    // Open the poster and come back: the same query and results.
    await page.getByTestId('search-titles').getByTestId('tile').tap()
    await expect(page).toHaveURL(/\/title\//)
    await page.goBack()
    await expect(page).toHaveURL(/\/search\?q=harbor$/)
    await expect(searchBox(page)).toHaveValue('harbor')
    await expect(page.getByTestId('search-titles')).toBeVisible()
    // And Back once more leaves search for Home.
    await page.goBack()
    await expect(page.getByTestId('home')).toBeVisible()

    for (const [query, heading] of [['zzzz', 'No results for “zzzz”'], ['metro', 'Channels']] as const) {
      await page.getByTestId('nav-search').tap()
      await expect(page).toHaveURL(/\/search$/)
      await expect(searchBox(page)).toHaveValue('')
      await searchBox(page).fill(query)
      await expect(page.getByTestId('search').getByRole('heading', { name: heading, exact: true })).toBeVisible()
      expect(await fits(page)).toBe(true)
    }
  })
})
