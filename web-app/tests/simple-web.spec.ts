import { expect, test, type Browser, type Locator, type Page } from '@playwright/test'
import { activeFilter, openFilters } from './helpers'

// Phase 2b: the app opens on content, on a phone, a tablet and a TV browser, by touch and by remote.
const PROFILES = {
  guest: { id: 'p2', name: 'Guest', avatar: null, is_admin: false, theme_id: 'midnight', languages: ['eng', 'spa'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' },
  owner: { id: 'p1', name: 'Owner', avatar: null, is_admin: true, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' },
}
const PHONE = { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true }

/** Start signed in, past the first-run notice. */
async function enter(page: Page, path = '/', who: keyof typeof PROFILES = 'guest') {
  await page.addInitScript((profile) => {
    sessionStorage.setItem('mock.accepted', JSON.stringify(['p1', 'p2', 'p3']))
    if (!localStorage.getItem('sh.session.v1')) localStorage.setItem('sh.session.v1', JSON.stringify({ access: `mock-access-${profile.id}`, refresh: `mock-refresh-${profile.id}`, profile }))
  }, PROFILES[who])
  await page.goto(path)
}
/** Answer one API path with fixed JSON, before the app boots. */
const stub = (page: Page, answers: Record<string, unknown>) => page.addInitScript((answers) => {
  const original = window.fetch.bind(window)
  window.fetch = async (input, init) => {
    const request = new Request(input, init)
    const answer = answers[new URL(request.url).pathname]
    return answer === undefined ? original(request) : Response.json(answer)
  }
}, answers)
async function other(browser: Browser, options: Parameters<Browser['newContext']>[0] = {}) {
  const context = await browser.newContext({ baseURL: test.info().project.use.baseURL, ...options })
  return { context, page: await context.newPage() }
}
const fits = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
const bottom = async (target: Locator) => { const box = await target.boundingBox(); return box!.y + box!.height }
const middle = async (target: Locator) => { const box = await target.boundingBox(); return box!.y + box!.height / 2 }
const focused = (page: Page) => page.locator('.fx[data-focused="true"]').last()
async function walk(page: Page, key: string, target: Locator, limit = 16) {
  for (let i = 0; i < limit && (await target.getAttribute('data-focused')) !== 'true'; i++) await page.keyboard.press(key)
  await expect(target).toHaveAttribute('data-focused', 'true')
}

test.describe('on a phone', () => {
  test.use(PHONE)

  test('Home has five labelled destinations and reaches a poster row without scrolling', async ({ page }) => {
    await enter(page)
    await expect(page.getByTestId('home')).toBeVisible()
    const bar = page.getByTestId('rail')
    await expect(bar.locator('.rail-item .lbl')).toHaveText(['Home', 'Search', 'Live TV', 'Library', 'More'])
    for (const label of await bar.locator('.rail-item .lbl').all()) await expect(label).toBeVisible()
    await expect(bar.locator('.rail-item svg')).toHaveCount(5)
    // The app download is not a content shortcut any more.
    await expect(page.getByTestId('android-download')).toHaveCount(0)
    await expect(page.getByTestId('home').getByRole('button', { name: 'Sports', exact: true })).toHaveCount(0)
    const top = (await bar.boundingBox())!.y
    expect(await bottom(page.locator('[data-row="shelf:movies:New Releases"] .tile-poster').first())).toBeLessThanOrEqual(top)
    expect(await fits(page)).toBe(true)
  })

  test('Library opens Movies on shelves: one search line, three shelves on the first screen', async ({ page }) => {
    await enter(page)
    await page.getByTestId('nav-library').tap()
    await expect(page).toHaveURL(/\/browse\/movies$/)
    const browse = page.getByTestId('browse')
    await expect(browse).toHaveAttribute('data-view', 'shelves')
    const rows = page.getByTestId('row')
    await expect(rows.first()).toHaveAttribute('aria-label', 'New Releases')
    // Before the first poster: the Movies/Shows switch, then search and Filter side by side. Nothing else.
    await expect(page.getByRole('group', { name: 'Library', exact: true }).getByRole('button')).toHaveText(['Movies', 'Shows'])
    await expect(browse.locator('.browse-head').getByRole('button')).toHaveCount(3)
    expect(Math.abs((await middle(page.getByLabel('Search this library'))) - (await middle(page.getByTestId('browse-filter'))))).toBeLessThan(4)
    // Arriving does not put the caret in the search field (that would raise the keyboard).
    await expect(page.getByLabel('Search this library')).not.toBeFocused()
    const top = (await page.getByTestId('rail').boundingBox())!.y
    for (const i of [0, 1, 2]) expect(await bottom(rows.nth(i).locator('.tile-poster').first())).toBeLessThanOrEqual(top)
    expect(new Set(await rows.evaluateAll((all) => all.slice(0, 3).map((row) => row.getAttribute('aria-label')))).size).toBe(3)
    expect(await fits(page)).toBe(true)

    // The last tile of a shelf lists all of it; Back returns to the shelves.
    const drama = page.locator('[data-row="shelf:Drama"]')
    await drama.getByTestId('see-all').scrollIntoViewIfNeeded()
    await expect(drama.getByTestId('see-all')).toContainText('7 titles')
    await drama.getByTestId('see-all').tap()
    await expect(page).toHaveURL(/\/browse\/movies\?category=Drama$/)
    await expect(browse).toHaveAttribute('data-view', 'grid')
    await expect(activeFilter(page, 'Category: Drama')).toBeVisible()
    await expect(browse.getByTestId('tile')).toHaveCount(7)
    expect(await fits(page)).toBe(true)
    await page.goBack()
    await expect(browse).toHaveAttribute('data-view', 'shelves')

    await page.getByRole('button', { name: 'Shows', exact: true }).tap()
    await expect(page).toHaveURL(/\/browse\/shows$/)
    await expect(rows.first()).toHaveAttribute('aria-label', 'TV Shows')
  })

  test('Filter is a bottom sheet; a filter in force is named under the search field and clears in one tap', async ({ page }) => {
    await enter(page, '/browse/movies')
    await expect(page.getByTestId('row').first()).toBeVisible()
    await page.getByTestId('browse-filter').tap()
    const sheet = page.getByTestId('browse-filters')
    await expect(sheet).toBeVisible()
    const box = (await sheet.boundingBox())!
    expect([box.x, box.width, Math.round(box.y + box.height)]).toEqual([0, 390, 844])
    await sheet.getByRole('button', { name: 'Rating', exact: true }).tap()
    await sheet.getByRole('button', { name: 'Done', exact: true }).tap()
    await expect(sheet).toHaveCount(0)
    await expect(page.getByTestId('browse')).toHaveAttribute('data-view', 'grid')
    await expect(activeFilter(page, 'Sort: Rating')).toBeVisible()
    await expect(page.getByTestId('browse').getByTestId('tile').first()).toHaveAttribute('aria-label', 'Neon Cartographers')
    await page.getByRole('button', { name: 'Clear filters', exact: true }).tap()
    await expect(page.getByTestId('browse')).toHaveAttribute('data-view', 'shelves')
    await expect(page.getByTestId('active-filter')).toHaveCount(0)
  })

  test('title page: one row of primary actions, the rest in a menu, one audio and version control', async ({ page }) => {
    await enter(page, '/title/tmdb%3Amovie%3A103')
    const play = page.getByTestId('title-play'), list = page.getByRole('button', { name: 'My List', exact: true }), more = page.getByTestId('title-more')
    await expect(play).toContainText('Resume')
    const line = await middle(play)
    expect(Math.abs((await middle(list)) - line)).toBeLessThan(2)
    expect(Math.abs((await middle(more)) - line)).toBeLessThan(2)
    await expect(page.getByTestId('title-detail').getByRole('button', { name: /Trailer|Favorite|Mark watched/ })).toHaveCount(0)
    // The audio and version pickers are one control that states what Play will use.
    await expect(page.getByTestId('title-audio-tracks')).toHaveCount(0)
    const choice = page.getByTestId('title-source')
    await expect(choice).toContainText('Audio & version')
    await expect(choice).toContainText('English · 5.1 · AC-3 · Plex')
    expect(await bottom(choice)).toBeLessThanOrEqual((await page.getByTestId('rail').boundingBox())!.y)
    expect(await fits(page)).toBe(true)
    await choice.tap()
    const dialog = page.getByRole('dialog', { name: 'Audio & version', exact: true })
    await expect(dialog.getByTestId('title-audio-tracks')).toBeVisible()
    await expect(dialog.getByRole('button', { name: /^Plex · / })).toBeVisible()
    await dialog.getByRole('button', { name: 'French · Stereo · AAC', exact: true }).tap()
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).tap()
    await expect(choice).toContainText('French · Stereo · AAC · Plex')

    await more.tap()
    const menu = page.getByTestId('title-more-menu')
    await expect(menu.locator('.menu-item')).toHaveText(['Add to favorites', 'Trailer', 'Mark watched'])
    await menu.getByRole('button', { name: 'Add to favorites', exact: true }).tap()
    await expect(menu.getByRole('button', { name: 'Remove from favorites', exact: true })).toBeVisible()
    await menu.getByRole('button', { name: 'Close', exact: true }).tap()
    await expect(menu).toHaveCount(0)
    await list.tap()
    await expect(list).toHaveAttribute('aria-pressed', 'true')
  })

  test('Live TV: groups in one scrolling row, no preview pane, a tap plays the channel', async ({ page }) => {
    await enter(page, '/live')
    await expect(page.getByTestId('channel-row').first()).toBeVisible()
    await expect(page.getByTestId('live-preview')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Watch full screen', exact: true })).toHaveCount(0)
    const groups = page.getByRole('group', { name: 'Channel groups', exact: true })
    await expect(groups.getByRole('button')).toHaveText(['All', 'Favorites', 'News', 'Sports', 'Movies', 'Kids', 'Music', 'Local'])
    // One line that scrolls sideways; the page itself does not.
    expect(await groups.evaluate((row) => row.scrollWidth > row.clientWidth && new Set(Array.from(row.children, (chip) => chip.getBoundingClientRect().top)).size === 1)).toBe(true)
    expect(await fits(page)).toBe(true)
    await groups.getByRole('button', { name: 'Favorites', exact: true }).tap()
    await expect(page.getByTestId('channel-row')).toHaveText([/Metro Sports 1/, /Static Radio TV/])
    await groups.getByRole('button', { name: 'Kids', exact: true }).scrollIntoViewIfNeeded()
    await groups.getByRole('button', { name: 'Kids', exact: true }).tap()
    await expect(groups.getByRole('button', { name: 'Favorites', exact: true })).toHaveAttribute('aria-pressed', 'false')
    await expect(page.getByTestId('channel-row').first()).toContainText('Little Orbit')
    await page.getByTestId('channel-row').first().tap()
    await expect(page).toHaveURL(/\/play\/ch/)
  })

  test('More holds My List, the remaining destinations and the profile', async ({ page }) => {
    await enter(page)
    await page.getByTestId('nav-more').tap()
    const menu = page.getByTestId('more-menu')
    await expect(menu.getByRole('link')).toHaveText(['My List', 'Sports', 'Requests', 'Settings', /Guest$/])
    const box = (await menu.boundingBox())!
    expect([box.x, box.width, Math.round(box.y + box.height)]).toEqual([0, 390, 844])
    await menu.getByRole('link', { name: 'My List', exact: true }).tap()
    await expect(page).toHaveURL(/\/favorites$/)
    await expect(page.getByRole('heading', { name: 'My List', exact: true })).toBeVisible()
    await expect(page.getByTestId('nav-more')).toHaveAttribute('aria-current', 'page')
  })
})

test('keyboard only: rail to Movies, along a shelf to See all and back, then the Filter panel', async ({ page }) => {
  await enter(page)
  await expect(page.getByTestId('hero-play')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowLeft')
  const rail = page.getByTestId('rail')
  await expect(rail).toHaveAttribute('data-expanded', 'true')
  // Focus names every destination, and no two share an icon.
  await expect(rail.locator('.rail-item .lbl')).toHaveText(['Home', 'Search', 'Live TV', 'Movies', 'Shows', 'My List', 'More', 'Guest'])
  await expect(page.getByTestId('nav-movies').locator('.lbl')).toHaveCSS('opacity', '1')
  const icons = await rail.locator('.rail-item svg path').evaluateAll((paths) => paths.map((path) => path.getAttribute('d')))
  expect(new Set(icons).size).toBe(7)
  await walk(page, 'ArrowDown', page.getByTestId('nav-movies'))
  await page.keyboard.press('Enter')

  // Movies arrives on the first poster, not on a control.
  await expect(page).toHaveURL(/\/browse\/movies$/)
  await expect(page.locator('[data-row="shelf:New Releases"] [data-testid="tile"]').first()).toHaveAttribute('data-focused', 'true')
  await expect(rail).toHaveAttribute('data-expanded', 'false')
  await page.keyboard.press('ArrowDown')
  const drama = page.locator('[data-row="shelf:Drama"]')
  await expect(drama.locator('.fx[data-focused="true"]')).toHaveCount(1)
  await walk(page, 'ArrowRight', drama.getByTestId('see-all'))
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/browse\/movies\?category=Drama$/)
  await expect(page.getByTestId('browse').getByTestId('tile').first()).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Escape')
  await expect(page).toHaveURL(/\/browse\/movies$/)
  await expect(drama.getByTestId('see-all')).toHaveAttribute('data-focused', 'true')

  // Up to the header and across to Filter; the panel opens on the current sort and closes back onto Filter.
  await page.keyboard.press('ArrowUp')
  await page.keyboard.press('ArrowUp')
  await walk(page, 'ArrowRight', page.getByTestId('browse-filter'), 4)
  await page.keyboard.press('Enter')
  const panel = page.getByTestId('browse-filters')
  await expect(panel.getByRole('button', { name: 'Recently added', exact: true })).toHaveAttribute('data-focused', 'true')
  const box = (await panel.boundingBox())!
  expect([Math.round(box.x + box.width), Math.round(box.height)]).toEqual([1280, 720]) // docked to the right edge
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('Enter')
  await expect(panel.getByRole('button', { name: 'Title', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.keyboard.press('Escape')
  await expect(panel).toHaveCount(0)
  await expect(page.getByTestId('browse-filter')).toHaveAttribute('data-focused', 'true')
  await expect(activeFilter(page, 'Sort: Title')).toBeVisible()
  await expect(page.getByTestId('browse')).toHaveAttribute('data-view', 'grid')
  // Removing the only filter by its chip returns to the shelves and leaves focus on Filter.
  await page.keyboard.press('ArrowDown')
  await expect(focused(page)).toHaveAttribute('aria-label', 'Remove filter Sort: Title')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('browse')).toHaveAttribute('data-view', 'shelves')
  await expect(page.getByTestId('browse-filter')).toHaveAttribute('data-focused', 'true')
  await expect(page.locator('.fx[data-focused="true"]')).toHaveCount(1)
})

test('Home is composed from shelves: New Releases, shelves with See all, Recently Added last', async ({ page }) => {
  await enter(page)
  const rows = page.getByTestId('row')
  await expect(rows.first()).toBeVisible()
  const ids = await rows.evaluateAll((all) => all.map((row) => row.getAttribute('data-row')))
  expect(ids.slice(0, 5)).toEqual(['continue', 'onnow', 'shelf:movies:New Releases', 'shelf:shows:TV Shows', 'shelf:movies:Drama'])
  expect(ids.at(-1)).toBe('recent')
  // Every row is compact: posters for titles, a captioned wide tile for what is in progress.
  await expect(page.locator('.feature-card')).toHaveCount(0)
  await expect(page.locator('[data-row="continue"] .tile-landscape .cap').first()).toHaveCSS('opacity', '1')
  // The hero features the New Releases shelf.
  await expect(page.getByTestId('hero-title')).toHaveText('Neon Cartographers')
  // First screen at 1280x720: the hero, what is in progress, what is on, and the first shelf's posters.
  const poster = (await page.locator('[data-row="shelf:movies:New Releases"] .tile-poster').first().boundingBox())!
  expect(poster.y + poster.height / 2).toBeLessThan(720)
  await expect(page.locator('[data-row="shelf:shows:Netflix"]')).toHaveAttribute('aria-label', 'Netflix · Shows')
  await page.locator('[data-row="shelf:shows:Netflix"]').getByTestId('see-all').click()
  await expect(page).toHaveURL(/\/browse\/shows\?category=Netflix$/)
  await expect(page.getByTestId('browse').getByTestId('tile')).toHaveCount(4)
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('home')).toBeVisible()
  await page.locator('[data-row="onnow"]').getByTestId('see-all').click()
  await expect(page).toHaveURL(/\/live$/)
})

test('More offers only what the profile may use, and closes back onto its opener', async ({ page, browser }) => {
  await enter(page)
  await page.getByTestId('nav-more').click()
  const menu = page.getByTestId('more-menu')
  // Recordings, Sources and Users are the owner's.
  await expect(menu.getByRole('link')).toHaveText(['Sports', 'Requests', 'Settings'])
  await expect(page.locator('.fx[data-focused="true"]')).toHaveText('Sports')
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/requests$/)
  await expect(page.getByTestId('nav-more')).toHaveAttribute('aria-current', 'page')
  await page.getByTestId('nav-more').click()
  await page.keyboard.press('Escape')
  await expect(menu).toHaveCount(0)
  await expect(page.getByTestId('nav-more')).toHaveAttribute('data-focused', 'true')
  // The old destinations still open by address.
  for (const [path, id] of [['/sources', 'sources'], ['/favorites', 'favorites'], ['/guide', 'guide'], ['/discover/movies', 'discover'], ['/pair', 'pair']] as const) {
    await page.goto(path)
    await expect(page.getByTestId(id)).toBeVisible()
  }

  const owner = await other(browser)
  await enter(owner.page, '/', 'owner')
  await owner.page.getByTestId('nav-more').click()
  await expect(owner.page.getByTestId('more-menu').getByRole('link')).toHaveText(['Sports', 'Requests', 'Recordings', 'Sources', 'Users', 'Settings'])
  await owner.context.close()
})

test('a tablet without hover names the rail icons while it is collapsed', async ({ browser }) => {
  const tablet = await other(browser, { viewport: { width: 820, height: 1180 }, hasTouch: true, isMobile: true })
  await enter(tablet.page)
  const rail = tablet.page.getByTestId('rail')
  await expect(rail.locator('.rail-item .lbl').first()).toBeVisible()
  expect(await tablet.page.evaluate(() => matchMedia('(hover: none)').matches)).toBe(true)
  for (const label of await rail.locator('.rail-item .lbl').all()) await expect(label).toHaveCSS('opacity', '1')
  expect((await rail.boundingBox())!.x).toBe(0)
  expect(await fits(tablet.page)).toBe(true)
  const rows = tablet.page.getByTestId('row')
  for (const i of [0, 1, 2, 3]) expect(await bottom(rows.nth(i))).toBeLessThanOrEqual(1180)
  await tablet.context.close()
})

test('Live TV leaves out a language or region row that offers one choice', async ({ page, browser }) => {
  // One channel language, and every channel this profile lists is in one region.
  await stub(page, { '/api/v1/channels/languages': [{ code: 'eng', name: 'English', count: 22 }] })
  await enter(page, '/live', 'owner')
  await expect(page.getByTestId('channel-row').first()).toHaveAttribute('data-focused', 'true')
  await expect(page.getByTestId('live')).toContainText('22 channels')
  await expect(page.getByRole('group', { name: 'Language', exact: true })).toHaveCount(0)
  await expect(page.getByRole('group', { name: 'Region', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /^Source:/ })).toHaveCount(0)
  await expect(page.getByTestId('live').getByText(/Region uses|sync with this profile/)).toHaveCount(0)
  await expect(page.getByTestId('live-preview')).toBeVisible() // wide screens keep the preview pane

  // Several languages and regions: both rows are back.
  const mixed = await other(browser)
  await enter(mixed.page, '/live')
  await expect(mixed.page.getByRole('group', { name: 'Language', exact: true })).toBeVisible()
  await expect(mixed.page.getByRole('group', { name: 'Region', exact: true })).toBeVisible()
  await mixed.context.close()
})

test('a one-language catalog is not hidden from a profile that prefers another language', async ({ page }) => {
  await stub(page, {
    '/api/v1/browse/movies/languages': [{ code: 'und', name: 'Unknown', count: 12 }],
    '/api/v1/browse/movies/shelves': { shelves: [] },
  })
  const asked: string[] = []
  page.on('request', (request) => { if (new URL(request.url()).pathname === '/api/v1/browse/movies') asked.push(new URL(request.url()).searchParams.get('language') ?? '') })
  await enter(page, '/browse/movies')
  // No shelves (a library without categories): the grid, with nothing in force.
  await expect(page.getByTestId('browse')).toHaveAttribute('data-view', 'grid')
  await expect(page.getByTestId('browse').getByTestId('tile')).toHaveCount(12)
  await expect(page.getByTestId('active-filter')).toHaveCount(0)
  await expect.poll(() => asked.at(-1)).toBe('all')
})

test('the Filter panel leaves out source and language when either has one value', async ({ page, browser }) => {
  await stub(page, { '/api/v1/sources': [{ id: 's_plex', type: 'plex', name: 'Den Plex', enabled: true, priority: 1, region: 'US', status: 'ok', last_sync: null, counts: { movies: 12 }, service_key: null, config_summary: {} }] })
  await enter(page, '/browse/movies')
  await openFilters(page)
  const panel = page.getByTestId('browse-filters')
  await expect(panel.getByRole('button', { name: /^Category:/ })).toBeVisible()
  await expect(panel.getByRole('button', { name: /^Genre:/ })).toBeVisible()
  await expect(panel.getByRole('button', { name: /^Source:/ })).toHaveCount(0)
  await expect(panel.getByRole('group', { name: 'Language', exact: true })).toHaveCount(0)

  const mixed = await other(browser)
  await stub(mixed.page, { '/api/v1/browse/movies/languages': [{ code: 'eng', name: 'English', count: 10 }, { code: 'spa', name: 'Spanish', count: 2 }] })
  await enter(mixed.page, '/browse/movies')
  await openFilters(mixed.page)
  await expect(mixed.page.getByTestId('browse-filters').getByRole('button', { name: 'Source: All sources', exact: true })).toBeVisible()
  await expect(mixed.page.getByTestId('browse-filters').getByRole('group', { name: 'Language', exact: true })).toBeVisible()
  await mixed.context.close()
})

test('My List gathers favorites, watchlist and history as tabs', async ({ page }) => {
  await enter(page)
  await page.getByTestId('nav-my-list').click()
  await expect(page).toHaveURL(/\/favorites$/)
  await expect(page.getByRole('heading', { name: 'My List', exact: true })).toBeVisible()
  const list = page.getByTestId('favorites')
  await expect(list.getByRole('tablist').getByRole('button')).toHaveText(['Favorites', 'Watchlist', 'History'])
  await expect(page.getByTestId('mylist-favorites')).toHaveAttribute('data-focused', 'true')
  await expect(list.getByTestId('tile')).toHaveText([/Metro Sports 1/, /Static Radio TV/, /Harbor Lights/])
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/favorites\?tab=watchlist$/)
  await expect(page.getByTestId('mylist-watchlist')).toHaveAttribute('data-focused', 'true')
  await expect(list.getByTestId('tile')).toHaveText([/Field Notes from Mars/, /Tidal Hymn/, /Signal Fire/])
  await page.getByTestId('mylist-history').click()
  await expect(page).toHaveURL(/\/favorites\?tab=history$/)
  await expect(list.getByTestId('tile')).toHaveText([/Harbor Lights/, /Neon Cartographers/])
  // Home's Watchlist row links to its tab.
  await page.goto('/favorites?tab=watchlist')
  await expect(page.getByTestId('mylist-watchlist')).toHaveAttribute('aria-pressed', 'true')
})

test('Search lists titles no source has under their own heading and leads to the metadata catalog', async ({ page }) => {
  await enter(page, '/search')
  await page.getByLabel('Search titles and channels').fill('velvet')
  const missing = page.getByTestId('group-missing')
  await expect(missing.getByRole('heading', { name: 'Not in your library', exact: true })).toBeVisible()
  await expect(missing.getByTestId('tile')).toHaveAttribute('aria-label', 'Velvet Antarctica')
  await expect(page.getByTestId('group-relevance')).toHaveCount(0)
  await page.getByRole('button', { name: 'Popular movies', exact: true }).click()
  await expect(page).toHaveURL(/\/discover\/movies$/)
  await expect(page.getByTestId('discover')).toBeVisible()
})

test('TV browser at 1920x1080: Movies shows three shelves on the first screen and the title page keeps its action row', async ({ browser }) => {
  const tv = await other(browser, { viewport: { width: 1920, height: 1080 } })
  await enter(tv.page, '/browse/movies')
  const rows = tv.page.getByTestId('row')
  await expect(rows.first()).toBeVisible()
  // Title, search and Filter share the one line above the shelves.
  const line = await middle(tv.page.getByRole('heading', { name: 'Movies', exact: true }))
  expect(Math.abs((await middle(tv.page.getByLabel('Search this library'))) - line)).toBeLessThan(6)
  expect(Math.abs((await middle(tv.page.getByTestId('browse-filter'))) - line)).toBeLessThan(6)
  for (const i of [0, 1]) expect(await bottom(rows.nth(i).locator('.tile-poster').first())).toBeLessThanOrEqual(1080)
  const third = (await rows.nth(2).locator('.tile-poster').first().boundingBox())!
  expect(third.y + third.height / 2).toBeLessThan(1080) // most of the third shelf's posters
  expect(await fits(tv.page)).toBe(true)

  await tv.page.goto('/title/tmdb%3Amovie%3A103')
  await expect(tv.page.getByTestId('title-play')).toHaveAttribute('data-focused', 'true')
  await expect(tv.page.getByRole('button', { name: 'Choose source', exact: true })).toBeVisible()
  await expect(tv.page.getByRole('button', { name: 'Trailer', exact: true })).toBeVisible()
  await expect(tv.page.getByTestId('title-audio-tracks')).toBeVisible()
  await expect(tv.page.getByTestId('title-more')).toHaveCount(0)
  // The poster sits beside the text, not a screen-width away from it.
  const poster = (await tv.page.locator('.title-poster').boundingBox())!, head = (await tv.page.getByRole('heading', { level: 1 }).boundingBox())!
  expect(head.x - (poster.x + poster.width)).toBeLessThan(60)
  await tv.context.close()
})

// Two things real data showed up: channel logos of any shape sat under the channel's text, and a
// catalog with one source repeated that source on every tile.
test.describe('review of the first pass', () => {
  /** The mock's home feed, with On Now logos of awkward shapes and one row of tiles with chosen sources. */
  const realistic = (page: Page) => page.addInitScript(() => {
    const original = window.fetch.bind(window)
    const svg = (w: number, h: number) => `data:image/svg+xml;utf8,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}"><rect width="${w}" height="${h}" fill="#fff"/></svg>`)}`
    const iptv = (id: string, name: string) => ({ source_id: id, source_type: 'xtream', name, playable_in_app: true, kind: 'vod' })
    const title = (n: number, name: string, sources: unknown[]) => ({ id: `tmdb:movie:${n}`, kind: 'movie', title: name, year: 2024, poster: null, backdrop: null, sources })
    window.fetch = async (input, init) => {
      const request = new Request(input, init)
      if (new URL(request.url).pathname !== '/api/v1/home') return original(request)
      const home = await (await original(request)).json() as { rows: Array<{ id: string; title: string; layout: string; tiles: Array<Record<string, unknown>> }> }
      const shapes = [svg(640, 80), svg(90, 400), svg(300, 300), null]
      for (const row of home.rows) if (row.id === 'onnow') row.tiles.forEach((tile, i) => {
        tile.logo = shapes[i % shapes.length]
        if (i === 0) { tile.title = 'International Documentary Channel HD Plus One'; (tile.now_playing as { title: string }).title = 'An Unusually Long Programme Title About Rivers, Maps And The People Who Draw Them' }
      })
      home.rows.splice(2, 0, { id: 'sources', title: 'Badge cases', layout: 'poster', tiles: [
        title(901, 'One Source', [iptv('s_xt', 'IPTV')]),
        title(902, 'Two Sources', [iptv('s_xt', 'IPTV'), iptv('s_x2', 'Second IPTV')]),
        title(903, 'Owned', [{ source_id: 's_plex', source_type: 'plex', name: 'Plex', playable_in_app: true, kind: 'library' }]),
        title(904, 'Owned Too', [iptv('s_xt', 'IPTV'), { source_id: 's_jf', source_type: 'jellyfin', name: 'Jellyfin', playable_in_app: true, kind: 'library' }]),
        title(905, 'Elsewhere', [{ source_id: 's_nf', source_type: 'streaming_service', name: 'Netflix', playable_in_app: false, kind: 'flatrate' }]),
        title(906, 'Nowhere', []),
      ] })
      return Response.json(home)
    }
  })
  const apart = (a: { x: number; y: number; width: number; height: number }, b: typeof a) =>
    a.x + a.width <= b.x + 0.5 || b.x + b.width <= a.x + 0.5 || a.y + a.height <= b.y + 0.5 || b.y + b.height <= a.y + 0.5
  const inside = (inner: { x: number; y: number; width: number; height: number }, outer: typeof inner) =>
    inner.x >= outer.x - 0.5 && inner.y >= outer.y - 0.5 && inner.x + inner.width <= outer.x + outer.width + 0.5 && inner.y + inner.height <= outer.y + outer.height + 0.5

  for (const [name, options] of [['a phone', PHONE], ['a TV browser', { viewport: { width: 1920, height: 1080 } }]] as const) {
    test(`On Now on ${name}: the channel logo and the channel's text never overlap`, async ({ browser }) => {
      const { context, page } = await other(browser, options)
      await realistic(page)
      await enter(page)
      const cards = page.locator('[data-row="onnow"] .tile-channel')
      await expect(cards.first()).toBeVisible()
      await expect(cards.first().locator('.channel-logo img')).toBeVisible()
      const count = Math.min(await cards.count(), 4)
      expect(count).toBe(4)
      for (let i = 0; i < count; i++) {
        const card = cards.nth(i)
        await card.scrollIntoViewIfNeeded()
        const [tile, logo, copy] = await Promise.all([card.boundingBox(), card.locator('.channel-logo').boundingBox(), card.locator('.channel-copy').boundingBox()])
        expect(apart(logo!, copy!), `card ${i}: logo and text`).toBe(true)
        expect(inside(logo!, tile!), `card ${i}: logo in card`).toBe(true)
        expect(inside(copy!, tile!), `card ${i}: text in card`).toBe(true)
        // The picture itself stays in its box whatever its shape; the words are cut, not spilled.
        const drawn = card.locator('.channel-logo img')
        if (await drawn.count()) expect(inside((await drawn.boundingBox())!, logo!), `card ${i}: image in box`).toBe(true)
        for (const line of await card.locator('.channel-copy > *').all()) expect(inside((await line.boundingBox())!, copy!), `card ${i}: line in text box`).toBe(true)
      }
      await expect(cards.first().locator('.channel-name')).toHaveText('2 · International Documentary Channel HD Plus One')
      await expect(cards.nth(3).getByTestId('img-fallback')).toBeVisible()
      expect(await fits(page)).toBe(true)
      await context.close()
    })
  }

  test('a tile names a source only when that says something', async ({ page }) => {
    await realistic(page)
    await enter(page)
    const tile = (title: string) => page.locator('[data-row="sources"]').getByRole('link', { name: title, exact: true })
    await expect(tile('One Source')).toBeVisible()
    // One ordinary source: nothing to say. A choice of sources, or a copy elsewhere only: say so.
    await expect(tile('One Source').locator('.badge')).toHaveCount(0)
    await expect(tile('Two Sources').locator('.badge')).toHaveText('Available · IPTV')
    await expect(tile('Elsewhere').locator('.badge')).toHaveText('Open · Netflix')
    await expect(tile('Nowhere').locator('.badge')).toHaveText('Not available here')
    // The household's own library is named as such, alone or among others.
    await expect(tile('Owned').locator('.badge')).toHaveText('In your library')
    await expect(tile('Owned Too').locator('.badge')).toHaveText('In your library')
    await expect(page.locator('.tile-badges').getByText(/Available · Plex/)).toHaveCount(0)
  })
})
