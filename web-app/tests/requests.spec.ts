import { expect, test, type Page } from '@playwright/test'
import { focusBarEntry, signIn } from './helpers'

const MOVIE = 'Velvet Antarctica'
const MOVIE_ID = 'tmdb:movie:301'
const MANAGER = 'http://requests.example.test:5055'

/** Something that happens elsewhere (an admin on another device, a library sync), sent straight to the mock API. */
async function elsewhere(page: Page, profile: string, method: string, path: string, body?: unknown) {
  return page.evaluate(async ({ profile, method, path, body }) => {
    const res = await fetch(`/api/v1${path}`, {
      method,
      headers: { authorization: `Bearer mock-access-${profile}`, 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    return { status: res.status, json: res.status === 202 || res.status === 204 ? null : ((await res.json()) as unknown) }
  }, { profile, method, path, body })
}

/** An admin has connected a request manager; the mock starts without one. */
async function connectManager(page: Page) {
  const saved = await elsewhere(page, 'p1', 'PUT', '/settings/request_manager', { url: MANAGER, api_key: 'fixture-manager-key' })
  expect(saved.json).toEqual({ url: MANAGER, api_key_set: true })
}

/** Reach the top bar with Up, walk it with Left/Right to a destination and open it. */
async function bar(page: Page, testId: string) {
  await focusBarEntry(page, testId)
  await page.keyboard.press('Enter')
}

/** Requests sits in the More menu: open it from the bar, walk down to the entry. */
async function more(page: Page, testId: string) {
  await bar(page, 'nav-more')
  const item = page.getByTestId('more-menu').getByTestId(testId)
  for (let i = 0; i < 8 && (await item.getAttribute('data-focused')) !== 'true'; i++) await page.keyboard.press('ArrowDown')
  await expect(item).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
}

const focused = (page: Page) => page.locator('.fx[data-focused="true"]').last()

test('keyboard only: request a movie, see it approved, open the notice when it arrives', async ({ page }) => {
  await signIn(page)
  await connectManager(page)
  await expect(page.getByTestId('hero-play')).toHaveAttribute('data-focused', 'true')
  await expect(page.getByTestId('notices')).toHaveCount(0)

  // Search finds a title no source has; its tile says so and opens the title page.
  await bar(page, 'nav-search')
  await expect(page.getByLabel('Search titles and channels')).toBeFocused()
  await page.keyboard.type('velvet')
  const result = page.getByTestId('group-missing').getByTestId('tile')
  await expect(result).toContainText('Not available', { timeout: 8000 })
  await page.keyboard.press('ArrowRight')
  await expect(result).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/title\/tmdb/)
  await expect(page.getByTestId('title-detail')).toContainText(MOVIE)

  // Request is the primary action and takes focus; afterwards the page shows the state instead.
  const request = page.getByTestId('title-request')
  await expect(request).toHaveAttribute('data-focused', 'true')
  await expect(request).toHaveClass(/btn-primary/)
  await expect(page.getByTestId('title-play')).toHaveCount(0)
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('title-request-state')).toHaveText('Requested · waiting for approval')
  await expect(request).toHaveCount(0)
  await expect(focused(page)).toHaveText('Watchlist')

  // My requests lists it; a viewer gets no approval controls.
  await more(page, 'nav-requests')
  await expect(page.getByTestId('requests')).toBeVisible()
  const row = page.getByTestId('request-row')
  await expect(row).toHaveCount(1)
  await expect(row).toHaveAttribute('data-state', 'pending_approval')
  await expect(row).toContainText(MOVIE)
  await expect(page.getByTestId('requests-tab-all')).toHaveCount(0)
  await expect(page.getByTestId('request-approve')).toHaveCount(0)

  // An admin approves on another device.
  expect((await elsewhere(page, 'p2', 'POST', '/requests/nope/approve')).status).toBe(403)
  const all = (await elsewhere(page, 'p1', 'GET', '/requests?scope=all')).json as { requests: Array<{ id: string; title_id: string }> }
  expect(all.requests.map((r) => r.title_id)).toEqual([MOVIE_ID])
  expect((await elsewhere(page, 'p1', 'POST', `/requests/${all.requests[0]!.id}/approve`, {})).status).toBe(200)
  await page.reload()
  await expect(row).toHaveAttribute('data-state', 'sent')
  await expect(row).toContainText('Approved · downloading')
  await expect(page.getByTestId('requests-tab-mine')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowDown')
  await expect(focused(page)).toHaveText('Details')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('title-request-state')).toHaveText('Approved · downloading')
  await expect(page.getByTestId('title-request')).toHaveCount(0)

  // The next library sync brings the title in; Home tells the requester.
  expect((await elsewhere(page, 'p1', 'POST', '/sources/s_plex/sync')).status).toBe(202)
  await bar(page, 'nav-home')
  const notice = page.getByTestId('notice')
  await expect(notice).toHaveCount(1)
  await expect(notice).toContainText(`${MOVIE} is now available`)
  await expect(page.getByTestId('hero-play')).toHaveAttribute('data-focused', 'true')
  const open = notice.getByRole('link')
  for (let i = 0; i < 4 && (await open.getAttribute('data-focused')) !== 'true'; i++) await page.keyboard.press('ArrowUp')
  await expect(open).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/title\/tmdb/)
  await expect(page.getByTestId('title-detail')).toContainText(MOVIE)
  await expect(page.getByTestId('title-play')).toBeVisible()
  await expect(page.getByTestId('title-request-state')).toHaveCount(0)
  await expect(page.getByTestId('title-request')).toHaveCount(0)

  // Opening it marked it read: the banner does not come back.
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('home')).toBeVisible()
  await expect(page.getByTestId('notices')).toHaveCount(0)
})

test('keyboard only: a notice can be dismissed without opening it', async ({ page }) => {
  await signIn(page)
  await connectManager(page)
  const created = await elsewhere(page, 'p2', 'POST', '/requests', { media_type: 'movie', tmdb_id: 301 })
  expect(created.status).toBe(201)
  await elsewhere(page, 'p1', 'POST', `/requests/${(created.json as { id: string }).id}/decline`, { note: 'Already on a service' })
  await page.reload()
  const notice = page.getByTestId('notice')
  await expect(notice).toContainText(`${MOVIE} was not approved`)
  await expect(notice).toContainText('Already on a service')
  await expect(page.getByTestId('hero-play')).toHaveAttribute('data-focused', 'true')
  const open = notice.getByRole('link')
  for (let i = 0; i < 4 && (await open.getAttribute('data-focused')) !== 'true'; i++) await page.keyboard.press('ArrowUp')
  await expect(open).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowRight')
  await expect(focused(page)).toHaveText('Dismiss')
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('notices')).toHaveCount(0)
  // Focus moved on before the banner left.
  await expect(focused(page)).toHaveText('Play')
  await expect(page).toHaveURL(/\/$/)
})

test.describe('as an admin', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      const profile = { id: 'p1', name: 'Owner', avatar: null, is_admin: true, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' }
      sessionStorage.setItem('mock.accepted', JSON.stringify(['p1', 'p2']))
      localStorage.setItem('sh.session.v1', JSON.stringify({ access: 'mock-access-p1', refresh: 'mock-refresh-p1', profile }))
    })
  })

  test('keyboard only: decline a pending request with a note; the requester is told', async ({ page }) => {
    await page.goto('/requests')
    await expect(page.getByTestId('requests')).toBeVisible()
    await connectManager(page)
    expect((await elsewhere(page, 'p2', 'POST', '/requests', { media_type: 'tv', tmdb_id: 302, seasons: [2] })).status).toBe(201)
    await page.reload()
    await expect(page.getByTestId('requests-tab-mine')).toHaveAttribute('data-focused', 'true')
    await expect(page.getByTestId('request-row')).toHaveCount(0)
    await page.keyboard.press('ArrowRight')
    await expect(page.getByTestId('requests-tab-all')).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('Enter')
    const row = page.getByTestId('request-row')
    await expect(row).toContainText('The Tollgate Tapes')
    await expect(row).toContainText('Season 2')
    await expect(row).toContainText('Requested by Guest')
    await page.keyboard.press('ArrowDown')
    await expect(focused(page)).toHaveText('Details')
    await page.keyboard.press('ArrowRight')
    await expect(focused(page)).toHaveText('Approve')
    await page.keyboard.press('ArrowRight')
    await expect(focused(page)).toHaveText('Decline')
    await page.keyboard.press('Enter')
    const dialog = page.getByRole('dialog')
    await expect(dialog).toContainText('Decline The Tollgate Tapes?')
    await expect(focused(page)).toHaveText('Keep request')
    await page.keyboard.press('ArrowUp')
    await expect(dialog.getByLabel('Note for the requester (optional)')).toBeFocused()
    await page.keyboard.type('Already on a service')
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('ArrowRight')
    await expect(focused(page)).toHaveText('Decline request')
    await page.keyboard.press('Enter')
    await expect(dialog).toHaveCount(0)
    await expect(row).toHaveAttribute('data-state', 'declined')
    await expect(row).toContainText('Already on a service')
    await expect(page.getByTestId('request-approve')).toHaveCount(0)
    // The decided row's buttons are gone; focus lands on what is left of it.
    await expect(focused(page)).toHaveText('Details')
    const told = (await elsewhere(page, 'p2', 'GET', '/notices')).json as { notices: Array<{ kind: string; body: string; ref_id: string }> }
    expect(told.notices.map((n) => [n.kind, n.body, n.ref_id])).toEqual([['request_declined', 'Already on a service', 'tmdb:tv:302']])
  })

  test('a series request can pick seasons, and an admin request is sent at once', async ({ page }) => {
    await page.goto('/requests')
    await expect(page.getByTestId('requests')).toBeVisible()
    await connectManager(page)
    await page.goto('/title/tmdb%3Atv%3A302')
    const request = page.getByTestId('title-request')
    await expect(request).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('Enter')
    const picker = page.getByTestId('request-seasons')
    await expect(picker).toBeVisible()
    await expect(focused(page)).toHaveText('All seasons')
    await page.keyboard.press('ArrowDown')
    await expect(focused(page)).toHaveText('Season 1')
    await page.keyboard.press('ArrowRight')
    await page.keyboard.press('Enter')
    await expect(picker.getByRole('button', { name: 'Season 2' })).toHaveAttribute('aria-pressed', 'true')
    await page.keyboard.press('ArrowDown')
    await expect(focused(page)).toHaveText('Request selected seasons')
    await page.keyboard.press('Enter')
    await expect(page.getByTestId('title-request-state')).toHaveText('Approved · downloading')
    const mine = (await elsewhere(page, 'p1', 'GET', '/requests')).json as { requests: Array<{ seasons: number[]; state: string }> }
    expect(mine.requests.map((r) => [r.seasons, r.state])).toEqual([[[2], 'sent']])
  })

  test('settings: connect, test and remove the request manager; the key is never shown', async ({ page }) => {
    await page.goto('/settings?tab=playback')
    const section = page.getByTestId('request-manager')
    await expect(section).toContainText('(not set)')
    await expect(page.getByTestId('request-manager-test')).toHaveAttribute('aria-disabled', 'true')
    await section.getByLabel('Request manager URL').fill(`${MANAGER}/`)
    await section.getByLabel('Request manager API key').fill('fixture-manager-key')
    await section.getByRole('button', { name: 'Save' }).click()
    await expect(section).toContainText('(connected)')
    await expect(section.getByLabel('Request manager URL')).toHaveValue(MANAGER)
    await expect(section.getByLabel('Request manager API key')).toHaveValue('')
    await expect(page.locator('body')).not.toContainText('fixture-manager-key')
    await page.getByTestId('request-manager-test').click()
    await expect(page.getByTestId('request-manager-result')).toHaveText('Connected to request manager 2.7.3.')
    // A title no source has becomes requestable once a manager is connected.
    const detail = (await elsewhere(page, 'p1', 'GET', '/titles/tmdb%3Amovie%3A301')).json as { requestable: boolean }
    expect(detail.requestable).toBe(true)
    await section.getByRole('button', { name: 'Remove' }).click()
    await expect(section).toContainText('(not set)')
    await expect(section.getByLabel('Request manager URL')).toHaveValue('')
  })
})

test('settings: a viewer does not get the request manager section', async ({ page }) => {
  await signIn(page)
  await page.goto('/settings?tab=playback')
  await expect(page.getByTestId('tab-playback')).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('request-manager')).toHaveCount(0)
})
