import { expect, test, type Page } from '@playwright/test'

// What a request says while the request manager works on it. The mock request manager (src/mocks/requests.ts)
// follows a script per title: 10 s of searching, then a download from second 10 to second 40 that ends in
// arriving (301), partly arriving (the series 302), failing at 22 s (303) or standing still from 20 s (304).
// The specs stop its clock and set it, so every percentage below is the script's and not the test's timing.
const MANAGER = 'http://requests.example.test:5055'
const MOVIE = 'Velvet Antarctica'
const SLOWEST_REFETCH = 20_000

async function api(page: Page, profile: string, method: string, path: string, body?: unknown) {
  return page.evaluate(async ({ profile, method, path, body }) => {
    const res = await fetch(`/api/v1${path}`, {
      method,
      headers: { authorization: `Bearer mock-access-${profile}`, 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    return { status: res.status, json: (await res.json().catch(() => null)) as unknown }
  }, { profile, method, path, body })
}

/** Change the mock API's stored requests in one step; the page's own requests see the result. */
async function store(page: Page, patch: Record<string, unknown>) {
  await page.evaluate((patch) => sessionStorage.setItem('mock.requests', JSON.stringify({ ...JSON.parse(sessionStorage.getItem('mock.requests') ?? '{}'), ...patch })), patch)
}
/** The request manager's clock, in seconds. */
const setClock = (page: Page, clock: number) => store(page, { clock })

const request = (page: Page, tmdbId: number, mediaType: 'movie' | 'tv' = 'movie', seasons?: number[]) =>
  api(page, 'p1', 'POST', '/requests', { media_type: mediaType, tmdb_id: tmdbId, seasons })

const rows = (page: Page) => page.getByTestId('request-row')
const rowOf = (page: Page, title: string) => rows(page).filter({ hasText: title })
const phases = (page: Page) => rows(page).evaluateAll((all) => all.map((row) => row.getAttribute('data-phase')))

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const profile = { id: 'p1', name: 'Owner', avatar: null, is_admin: true, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' }
    sessionStorage.setItem('mock.accepted', JSON.stringify(['p1', 'p2']))
    localStorage.setItem('sh.session.v1', JSON.stringify({ access: 'mock-access-p1', refresh: 'mock-refresh-p1', profile }))
  })
  await page.goto('/requests')
  await expect(page.getByTestId('requests')).toBeVisible()
  expect((await api(page, 'p1', 'PUT', '/settings/request_manager', { url: MANAGER, api_key: 'fixture-manager-key' })).status).toBe(200)
  await setClock(page, 0)
})

test('the title page follows a request from searching to available without a reload', async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto('/title/tmdb%3Amovie%3A301')
  await page.evaluate(() => { (window as unknown as { sameDocument: boolean }).sameDocument = true })
  const status = page.getByTestId('title-request-state')
  const chip = status.getByTestId('request-phase')
  const bar = status.getByTestId('request-bar')
  const text = status.getByTestId('request-progress-text')

  // The owner's request is approved and sent at once. All that is known is that the manager took it:
  // it says Searching, and has no bar, until the manager reports a download.
  await page.getByTestId('title-request').click()
  await expect(chip).toHaveText('Searching')
  await expect(bar).toHaveCount(0)
  await expect(page.getByTestId('title-request')).toHaveCount(0)
  await page.waitForTimeout(1500)
  await expect(chip).toHaveText('Searching')
  await expect(status).not.toContainText('Downloading')

  await setClock(page, 16)
  await expect(chip).toHaveText('Downloading', { timeout: SLOWEST_REFETCH })
  await expect(bar).toHaveAttribute('role', 'progressbar')
  await expect(bar).toHaveAttribute('aria-valuenow', '20')
  await expect(bar).toHaveAttribute('aria-valuemin', '0')
  await expect(bar).toHaveAttribute('aria-valuemax', '100')
  // The time left comes with the request itself, a moment after the title's own phase and percentage.
  await expect(bar).toHaveAttribute('aria-valuetext', '20%, about 24 min left', { timeout: SLOWEST_REFETCH })
  await expect(text).toHaveText('20% · about 24 min left')
  // The bar is rounded, and filled as far as it says.
  const shape = () => bar.evaluate((el) => {
    const fill = el.firstElementChild as HTMLElement
    return { radius: parseFloat(getComputedStyle(el).borderTopLeftRadius), height: el.clientHeight, filled: Math.round((fill.getBoundingClientRect().width / el.getBoundingClientRect().width) * 100) }
  })
  await expect.poll(async () => (await shape()).filled).toBe(20)
  expect((await shape()).radius).toBeGreaterThanOrEqual((await shape()).height / 2)

  // A hidden tab does not ask: the bar stays where it was until the tab is looked at again.
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' })
    document.dispatchEvent(new Event('visibilitychange'))
  })
  await setClock(page, 28)
  await page.waitForTimeout(7000)
  await expect(bar).toHaveAttribute('aria-valuenow', '20')
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
    document.dispatchEvent(new Event('visibilitychange'))
  })
  await expect(bar).toHaveAttribute('aria-valuenow', '60')
  await expect(text).toHaveText('60% · about 12 min left')

  await setClock(page, 37)
  await expect(bar).toHaveAttribute('aria-valuenow', '90')
  await setClock(page, 42)
  await expect(chip).toHaveText('Importing')
  await expect(bar).toHaveCount(0)

  // It arrives: the page offers Play, and Home has the notice.
  await setClock(page, 50)
  await expect(page.getByTestId('title-play')).toBeVisible()
  await expect(status).toHaveCount(0)
  expect(await page.evaluate(() => (window as unknown as { sameDocument?: boolean }).sameDocument)).toBe(true)
  const told = (await api(page, 'p1', 'GET', '/notices')).json as { notices: Array<{ kind: string; title: string }> }
  expect(told.notices.map((n) => [n.kind, n.title])).toEqual([['request_available', `${MOVIE} is now available`]])
})

test('the Requests screen shows each phase and keeps what is moving on top', async ({ page }) => {
  test.setTimeout(120_000)
  // Three requests at second 0, a fourth at second 30: at that moment one each is failed, stalled, downloading and searching.
  for (const [id, type, seasons] of [[303, 'movie'], [304, 'movie'], [302, 'tv', [1, 2]]] as const) expect((await request(page, id, type, seasons ? [...seasons] : undefined)).status).toBe(201)
  await setClock(page, 30)
  expect((await request(page, 301)).status).toBe(201)
  await page.reload()

  await expect(rows(page)).toHaveCount(4)
  expect(await phases(page)).toEqual(['downloading', 'stalled', 'searching', 'failed'])
  const series = rowOf(page, 'The Tollgate Tapes')
  await expect(series.getByTestId('request-phase')).toHaveText('Downloading')
  await expect(series.getByTestId('request-bar')).toHaveAttribute('aria-valuenow', '66')
  await expect(series.getByTestId('request-bar')).toHaveAttribute('aria-label', 'Download progress: The Tollgate Tapes')
  await expect(series.getByTestId('request-detail')).toHaveText('4 of 6 episodes')
  await expect(series.getByTestId('request-progress-text')).toHaveText('66% · about 10 min left')
  const stalled = rowOf(page, 'The Quiet Meridian')
  await expect(stalled.getByTestId('request-phase')).toHaveText('Stalled')
  await expect(stalled.getByTestId('request-phase')).toHaveClass(/badge-warn/)
  await expect(stalled.getByTestId('request-detail')).toHaveText('No progress for 30 minutes.')
  await expect(stalled.getByTestId('request-bar')).toHaveCount(0)
  const searching = rowOf(page, MOVIE)
  await expect(searching.getByTestId('request-phase')).toHaveText('Searching')
  await expect(searching.getByTestId('request-bar')).toHaveCount(0)
  const failed = rowOf(page, 'Harbour of Paper Boats')
  await expect(failed.getByTestId('request-phase')).toHaveText('Failed')
  await expect(failed.getByTestId('request-phase')).toHaveClass(/badge-warn/)
  await expect(failed.getByTestId('request-detail')).toHaveText('The request manager could not complete this request.')
  // The detail is said once, not again as the request's note.
  await expect(failed.getByText('The request manager could not complete this request.')).toHaveCount(1)

  // What the API offers for the two that need a look.
  await expect(searching.getByTestId('request-check')).toHaveCount(0)
  await stalled.getByTestId('request-check').click()
  await expect(page.getByText('Checked The Quiet Meridian: stalled.')).toBeVisible()

  // Sixteen seconds on: the film has started, the series has its first season. The order follows without a reload.
  await setClock(page, 46)
  await expect.poll(() => phases(page), { timeout: SLOWEST_REFETCH }).toEqual(['downloading', 'stalled', 'partially_available', 'failed'])
  await expect(searching.getByTestId('request-bar')).toHaveAttribute('aria-valuenow', '20')
  await expect(series.getByTestId('request-phase')).toHaveText('Partly available')
  await expect(series.getByTestId('request-detail')).toHaveText('1 of 2 seasons available')
  await setClock(page, 60)
  await expect(searching.getByTestId('request-bar')).toHaveAttribute('aria-valuenow', '66')

  // The film arrives: a success chip, and it joins what is over, under what is still open.
  await setClock(page, 80)
  await expect.poll(() => phases(page), { timeout: SLOWEST_REFETCH }).toEqual(['stalled', 'partially_available', 'available', 'failed'])
  await expect(searching.getByTestId('request-phase')).toHaveText('Available')
  await expect(searching.getByTestId('request-phase')).toHaveClass(/badge-ok/)

  // Asking again for the one that failed opens a new request, which starts over at Searching; the old row stays as history.
  await failed.getByTestId('request-again').click()
  await expect(rows(page)).toHaveCount(5)
  expect(await phases(page)).toEqual(['stalled', 'searching', 'partially_available', 'available', 'failed'])
  await expect(page.getByTestId('request-again')).toHaveCount(0)
})

test('requested, approved, declined and unknown: the phases nobody is downloading in', async ({ page }) => {
  // Somebody else asks; the owner sees it waiting on All requests, and declines it.
  expect((await api(page, 'p2', 'POST', '/requests', { media_type: 'movie', tmdb_id: 301 })).status).toBe(201)
  await page.goto('/requests?scope=all')
  const asked = rowOf(page, MOVIE)
  await expect(asked.getByTestId('request-phase')).toHaveText('Requested')
  await expect(asked).toContainText('Requested by Guest')
  await expect(asked.getByTestId('request-bar')).toHaveCount(0)
  const id = ((await api(page, 'p1', 'GET', '/requests?scope=all')).json as { requests: Array<{ id: string }> }).requests[0]!.id
  expect((await api(page, 'p1', 'POST', `/requests/${id}/decline`, { note: 'Already on a service' })).status).toBe(200)
  await page.reload()
  await expect(asked.getByTestId('request-phase')).toHaveText('Declined')
  await expect(asked.getByTestId('request-phase')).toHaveClass(/badge-warn/)
  await expect(asked.getByTestId('request-detail')).toHaveText('Already on a service')

  // What only a server can report (the mock request manager always answers), put straight into the mock's store.
  const base = { profile_id: 'p1', profile_name: 'Owner', media_type: 'movie', year: 2024, poster: null, seasons: null, note: null, decided_by_profile_id: 'p1', created_at: '2030-01-01T10:00:00Z', updated_at: '2030-01-01T10:00:00Z' }
  const progress = (phase: string, more: Record<string, unknown> = {}) => ({ phase, last_phase: null, percent: null, eta: null, detail: null, checked_at: '2030-01-01T10:00:00Z', ...more })
  await store(page, {
    requests: [
      { ...base, id: 'a1', tmdb_id: 901, title_id: 'tmdb:movie:901', title: 'Unsent fixture', state: 'approved', note: 'The request manager answered 500.', progress: progress('approved', { detail: 'The request manager answered 500.' }) },
      { ...base, id: 'a2', tmdb_id: 902, title_id: 'tmdb:movie:902', title: 'Unreachable fixture', state: 'sent', progress: progress('unknown', { last_phase: 'downloading', detail: 'The request manager did not answer.' }) },
      { ...base, id: 'a3', tmdb_id: 903, title_id: 'tmdb:movie:903', title: 'Unsized fixture', state: 'sent', progress: progress('downloading') },
    ],
  })
  await page.goto('/requests')
  await expect(rows(page)).toHaveCount(3)
  expect(await phases(page)).toEqual(['downloading', 'unknown', 'approved'])
  const unsent = rowOf(page, 'Unsent fixture')
  await expect(unsent.getByTestId('request-phase')).toHaveText('Approved')
  await expect(unsent.getByTestId('request-detail')).toHaveText('The request manager answered 500.')
  await expect(unsent.getByText('The request manager answered 500.')).toHaveCount(1)
  await expect(unsent.getByTestId('request-check')).toBeVisible()
  const unreachable = rowOf(page, 'Unreachable fixture')
  await expect(unreachable.getByTestId('request-phase')).toHaveText('Status unknown')
  await expect(unreachable.getByTestId('request-detail')).toHaveText('The request manager did not answer. Last known: downloading.')
  await expect(unreachable.getByTestId('request-bar')).toHaveCount(0)
  await expect(unreachable.getByTestId('request-check')).toBeVisible()

  // A download whose sizes are not known: a bar with no value, that says so in words and holds still on request.
  const unsized = rowOf(page, 'Unsized fixture').getByTestId('request-bar')
  await expect(unsized).toHaveAttribute('role', 'progressbar')
  await expect(unsized).not.toHaveAttribute('aria-valuenow', /.*/)
  await expect(unsized).toHaveAttribute('aria-valuetext', 'Amount not reported')
  await expect(rowOf(page, 'Unsized fixture').getByTestId('request-progress-text')).toHaveCount(0)
  const motion = () => unsized.evaluate((el) => getComputedStyle(el.firstElementChild!).animationName)
  expect(await motion()).toBe('request-bar-travel')
  await page.emulateMedia({ reducedMotion: 'reduce' })
  expect(await motion()).toBe('none')
})

test('a poster carries the same status: a chip in its corner, a thin bar along its foot while downloading', async ({ page }) => {
  expect((await request(page, 301)).status).toBe(201)
  await page.goto('/search')
  await page.getByLabel('Search titles and channels').fill('velvet')
  const tile = page.getByTestId('group-missing').getByTestId('tile')
  // Sent a moment ago: Searching, never Downloading, and no bar.
  await expect(tile.getByTestId('request-phase')).toHaveText('Searching')
  await expect(tile.getByTestId('request-bar')).toHaveCount(0)

  await setClock(page, 25)
  await page.reload()
  await page.getByLabel('Search titles and channels').fill('velvet')
  await expect(tile.getByTestId('request-phase')).toHaveText('Downloading 50%')
  const bar = tile.getByTestId('request-bar')
  await expect(bar).toHaveAttribute('aria-valuenow', '50')
  await expect(bar).toHaveAttribute('aria-valuetext', '50%')
  // Thin, rounded, inside the poster's foot.
  const fit = await bar.evaluate((el) => {
    const box = el.getBoundingClientRect(), poster = el.closest('[data-testid="tile"]')!.getBoundingClientRect()
    return { height: box.height, radius: parseFloat(getComputedStyle(el).borderTopLeftRadius), inside: box.left >= poster.left && box.right <= poster.right && box.bottom <= poster.bottom, fromFoot: poster.bottom - box.bottom }
  })
  expect(fit.height).toBeLessThanOrEqual(6)
  expect(fit.radius).toBeGreaterThanOrEqual(fit.height / 2)
  expect(fit.inside).toBe(true)
  expect(fit.fromFoot).toBeLessThan(12)
})
