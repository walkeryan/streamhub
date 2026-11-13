import { expect, test, type BrowserContext, type Page } from '@playwright/test'

// Phase 4: one person, one user. What a user may do comes from /auth/me, people sign in with Google
// and wait for approval, and the owner manages them on the Users screen. The mock keeps its
// household in localStorage, so two tabs of one context are two devices of the same household.
const profile = (id: string, name: string, is_admin = false) => ({ id, name, avatar: null, is_admin, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' })
const WHO = { owner: profile('p1', 'Ryan', true), guest: profile('p2', 'Guest'), kids: profile('p3', 'Kids') }
interface Setup { who?: keyof typeof WHO; shared?: boolean; mode?: 'legacy' | 'no-switch'; google?: string }

/** Prepare a tab: past the first-run notice, optionally signed in. A tab keeps its own session across reloads. */
async function prepare(page: Page, { who, shared, mode, google }: Setup = {}) {
  await page.addInitScript(({ session, shared, mode, google }) => {
    sessionStorage.setItem('mock.accepted', JSON.stringify(Array.from({ length: 40 }, (_, i) => `p${i + 1}`)))
    if (shared) localStorage.setItem('mock.device', 'shared')
    if (mode) localStorage.setItem('mock.people.mode', mode)
    if (google) sessionStorage.setItem('mock.google', google)
    if (sessionStorage.getItem('spec.tab')) return
    sessionStorage.setItem('spec.tab', '1')
    if (session) localStorage.setItem('sh.session.v1', JSON.stringify({ access: `mock-access-${session.id}`, refresh: `mock-refresh-${session.id}`, profile: session }))
    else localStorage.removeItem('sh.session.v1')
  }, { session: who ? WHO[who] : null, shared, mode, google })
}
async function enter(page: Page, path: string, setup: Setup = { who: 'owner' }) {
  await prepare(page, setup)
  await page.goto(path)
}
const more = async (page: Page) => {
  await page.getByTestId('nav-more').click()
  return page.getByTestId('more-menu').getByRole('link')
}
/** Something the owner does on another device: straight to the API, as that user. */
const asOwner = (page: Page, method: string, path: string, body?: unknown) => page.evaluate(async ({ method, path, body }) => {
  const response = await fetch(`/api/v1${path}`, { method, headers: { authorization: 'Bearer mock-access-p1', 'content-type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  return response.status
}, { method, path, body })
const second = async (context: BrowserContext) => context.newPage()

test.describe('permissions', () => {
  test('More follows the permissions /auth/me reports, not the admin flag', async ({ page, context }) => {
    // The stored profile claims to be an admin; the server says this user is Family.
    await prepare(page)
    await page.addInitScript((p) => localStorage.setItem('sh.session.v1', JSON.stringify({ access: 'mock-access-p2', refresh: 'mock-refresh-p2', profile: p })), { ...WHO.guest, is_admin: true })
    await page.goto('/')
    await expect(page.getByTestId('home')).toBeVisible()
    await expect(await more(page)).toHaveText(['Sports', 'Requests', 'Recordings', 'Settings'])
    const owner = await second(context)
    await enter(owner, '/')
    await expect(await more(owner)).toHaveText(['Sports', 'Requests', 'Recordings', 'Sources', 'Users', 'Settings'])
  })

  test('a server without /auth/me falls back to the admin flag and offers no Users screen', async ({ page, context }) => {
    await enter(page, '/', { who: 'owner', mode: 'legacy' })
    await expect(page.getByTestId('home')).toBeVisible()
    await expect(await more(page)).toHaveText(['Sports', 'Requests', 'Sources', 'Settings'])
    const guest = await second(context)
    await enter(guest, '/', { who: 'guest', mode: 'legacy' })
    await expect(await more(guest)).toHaveText(['Sports', 'Requests', 'Settings'])
  })

  test('a suspended user is signed out and told why', async ({ page }) => {
    await enter(page, '/', { who: 'guest' })
    await expect(page.getByTestId('home')).toBeVisible()
    expect(await asOwner(page, 'POST', '/users/u2/suspend')).toBe(200)
    // Their next request, whatever it is, is refused.
    await page.getByTestId('nav-live-tv').click()
    await expect(page.getByTestId('signed-out-notice')).toContainText('This account has been suspended')
    expect(await page.evaluate(() => localStorage.getItem('sh.session.v1'))).toBeNull()
  })
})

test.describe('refusals read the same everywhere', () => {
  /** Answer one API path with a refusal, before the app boots. */
  const refuse = (page: Page, path: string, status: number, body: unknown) => page.addInitScript(({ path, status, body }) => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const request = new Request(input, init)
      return new URL(request.url).pathname === path ? Response.json(body, { status }) : original(request)
    }
  }, { path, status, body })

  test('a missing permission', async ({ page }) => {
    await refuse(page, '/api/v1/users', 403, { code: 'forbidden', message: 'Missing permission: users.manage', missing: ['users.manage'] })
    await enter(page, '/users')
    await expect(page.getByRole('alert')).toContainText("You don't have permission to do this")
  })

  test('an account that only works at home can still sign out', async ({ page }) => {
    await refuse(page, '/api/v1/auth/me', 403, { code: 'remote_access_denied', message: 'remote access is not allowed for this user' })
    await enter(page, '/', { who: 'guest' })
    await expect(page.getByRole('alert')).toContainText('This account can only be used on the home network')
    await page.getByRole('button', { name: 'Sign out', exact: true }).click()
    await expect(page.getByTestId('profile-picker')).toBeVisible()
  })

  test('a stream limit names what is already playing, like a busy source', async ({ page }) => {
    await refuse(page, '/api/v1/playback/resolve', 409, { code: 'user_stream_limit', message: 'stream limit reached', limit: 1, active: [{ profile_name: 'Guest', title: 'Neon Cartographers', kind: 'movie', since: new Date().toISOString() }] })
    await enter(page, '/play/tmdb%3Amovie%3A101', { who: 'guest' })
    await expect(page.getByTestId('source-busy')).toHaveText('You are already watching something')
    await expect(page.getByTestId('player-error')).toContainText('Neon Cartographers is playing on another device')
    await expect(page.getByTestId('player-error').getByRole('button', { name: 'Try again', exact: true })).toBeVisible()
  })
})

test.describe('signing in', () => {
  const INVITE = 'inv-open'.padEnd(43, 'x'), SPENT = 'inv-used'.padEnd(43, 'x')
  /** Google's part of the trip: the server would redirect there and come back with ?google_complete=1. */
  const google = async (page: Page) => {
    const asked: string[] = []
    // On the context: the mock's service worker controls the page, and a page route does not see what passes through it.
    await page.context().route('**/api/v1/auth/google/start*', (route) => {
      asked.push(route.request().url())
      return route.fulfill({ status: 303, headers: { location: '/?google_complete=1' } })
    })
    return asked
  }

  test('the sign-in page is for everyone: approval is explained, no allowlist wording', async ({ page }) => {
    await enter(page, '/', { google: 'pending' })
    await expect(page.getByRole('heading', { name: 'Welcome to StreamHub' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Continue with Google', exact: true })).toHaveAttribute('data-focused', 'true')
    await expect(page.getByText('is asked to approve you')).toBeVisible()
    await expect(page.getByText(/authorized/i)).toHaveCount(0)
  })

  test('a new person waits for approval, then the same page becomes their session', async ({ page }) => {
    const asked = await google(page)
    await enter(page, '/', { google: 'pending' })
    const polls: string[] = []
    page.on('request', (r) => { if (r.url().endsWith('/api/v1/auth/me')) polls.push(r.headers().authorization ?? '') })
    await page.getByRole('button', { name: 'Continue with Google', exact: true }).click()
    const waiting = page.getByTestId('waiting')
    await expect(waiting.getByRole('heading', { name: 'Waiting for approval' })).toBeVisible()
    expect(new URL(asked[0]!).searchParams.get('return_to')).toBe('/')
    await expect(page.getByTestId('waiting-who')).toHaveText('Signed in as Nell Vance · nell@example.com')
    await expect(waiting.getByText('What happens next')).toBeVisible()
    await expect(waiting.getByRole('button', { name: 'Sign out', exact: true })).toBeVisible()
    await expect(page).toHaveURL(/\/$/)
    // Not signed in yet: nothing is stored as a session, and only /auth/me is asked, with the pending token.
    expect(await page.evaluate(() => localStorage.getItem('sh.session.v1'))).toBeNull()
    await expect(waiting.getByText('Not approved yet.')).toBeVisible()
    // A reload goes on waiting.
    await page.reload()
    await expect(waiting.getByRole('heading', { name: 'Waiting for approval' })).toBeVisible()

    const pending = await page.evaluate(async () => (await (await fetch('/api/v1/users?status=pending', { headers: { authorization: 'Bearer mock-access-p1' } })).json() as Array<{ id: string; display_name: string }>).find((u) => u.display_name === 'Nell Vance')!.id)
    expect(polls.length).toBeGreaterThan(1)
    expect(new Set(polls)).toEqual(new Set([`Bearer mock-pending-${pending}`]))
    expect(await asOwner(page, 'POST', `/users/${pending}/approve`, { role_id: 'friend' })).toBe(200)
    await expect(page.getByTestId('home')).toBeVisible({ timeout: 12_000 })
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem('sh.session.v1')!).profile.name)).toBe('Nell Vance')
    await expect(await more(page)).toHaveText(['Sports', 'Requests', 'Settings'])
  })

  test('a declined request says so; Sign out returns to the sign-in page', async ({ page }) => {
    await enter(page, '/?google_complete=1', { google: 'pending' })
    const waiting = page.getByTestId('waiting')
    await expect(waiting.getByRole('heading', { name: 'Waiting for approval' })).toBeVisible()
    const pending = await page.evaluate(async () => (await (await fetch('/api/v1/users?status=pending', { headers: { authorization: 'Bearer mock-access-p1' } })).json() as Array<{ id: string; display_name: string }>).find((u) => u.display_name === 'Nell Vance')!.id)
    expect(await asOwner(page, 'POST', `/users/${pending}/deny`)).toBe(204)
    await page.getByTestId('waiting-check').click()
    await expect(waiting.getByRole('heading', { name: 'This request is closed' })).toBeVisible()
    await expect(page.getByTestId('waiting-signout')).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('Enter')
    await expect(page.getByRole('button', { name: 'Continue with Google', exact: true })).toBeVisible()
  })

  test('an invite link explains itself and signs in with the invitation attached', async ({ page }) => {
    const asked = await google(page)
    await enter(page, `/invite/${INVITE}`, { google: `invite:${INVITE}` })
    const landing = page.getByTestId('invite')
    await expect(landing.getByRole('heading', { name: 'You are invited to StreamHub' })).toBeVisible()
    await expect(landing.getByRole('button', { name: 'Accept with Google', exact: true })).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('Enter')
    await expect(page.getByTestId('home')).toBeVisible()
    const start = new URL(asked[0]!)
    expect(start.pathname).toBe('/api/v1/auth/google/start')
    expect(Object.fromEntries(start.searchParams)).toEqual({ return_to: '/', invite: INVITE })
    // Active at once, with the invite's role.
    const me = await page.evaluate(async () => (await fetch('/api/v1/auth/me', { headers: { authorization: `Bearer ${JSON.parse(localStorage.getItem('sh.session.v1')!).access}` } })).json() as Promise<{ status: string; role: { id: string } }>)
    expect([me.status, me.role.id]).toEqual(['active', 'friend'])
  })

  test('a damaged link and a spent invitation each get a plain message', async ({ page, context }) => {
    await enter(page, '/invite/abc', { google: 'pending' })
    await expect(page.getByRole('heading', { name: 'This invitation link is not complete' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Accept with Google', exact: true })).toHaveCount(0)

    // Approval mode: the sign-in still succeeds, as a request for approval, and says the link did not count.
    const spent = await second(context)
    await google(spent)
    await enter(spent, `/invite/${SPENT}`, { google: `invite:${SPENT}` })
    await spent.getByTestId('invite-accept').click()
    await expect(spent.getByTestId('waiting')).toContainText('That invitation link could not be used')

    // Invite-only mode: the server refuses and the callback returns the reason.
    const refused = await second(context)
    await prepare(refused, { google: 'pending' })
    await refused.addInitScript(() => sessionStorage.setItem('sh.invite.v1', '1'))
    await refused.goto('/?google_error=invite_required')
    await expect(refused.getByRole('alert')).toContainText('That invitation can no longer be used')
    await refused.goto('/?google_error=user_suspended')
    await expect(refused.getByRole('alert')).toContainText('This account has been suspended')
  })
})

test.describe('the Users screen', () => {
  const row = (page: Page, name: string) => page.getByTestId('user-row').filter({ hasText: name })
  const toast = (page: Page) => page.locator('.toast').last()
  const focused = (page: Page) => page.locator('.fx[data-focused="true"]').last()
  async function walk(page: Page, key: string, target: ReturnType<Page['locator']>, limit = 40) {
    for (let i = 0; i < limit && (await target.getAttribute('data-focused')) !== 'true'; i++) await page.keyboard.press(key)
    await expect(target).toHaveAttribute('data-focused', 'true')
  }
  const fits = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)

  test('it is the owner\'s: listed in More, closed to everyone else', async ({ page, context }) => {
    await enter(page, '/')
    await page.getByTestId('nav-more').click()
    await page.getByTestId('more-menu').getByRole('link', { name: 'Users', exact: true }).click()
    await expect(page).toHaveURL(/\/users$/)
    await expect(page.getByTestId('users').getByRole('heading', { name: 'Users', exact: true })).toBeVisible()
    await expect(page.getByTestId('users-tab-people')).toHaveAttribute('data-focused', 'true')
    // name, sign-in type, role, status, last seen, now playing
    await expect(row(page, 'Mara Ellison')).toContainText('Google sign-in · Friend · Last seen 2 min ago')
    await expect(row(page, 'Mara Ellison').getByTestId('now-playing')).toHaveText('Watching Neon Cartographers')
    await expect(row(page, 'Theo Okafor')).toContainText('Suspended')
    await expect(row(page, 'Kids')).toContainText('Managed sign-in · Kid')
    await expect(page.getByTestId('pending-row')).toHaveCount(2)

    const guest = await second(context)
    await enter(guest, '/users', { who: 'guest' })
    await expect(guest.getByTestId('home')).toBeVisible()
    await expect(guest).toHaveURL(/\/$/)
  })

  test('the owner approves a waiting person with a role; their page opens by itself', async ({ page, context }) => {
    await enter(page, '/?google_complete=1', { google: 'pending' })
    await expect(page.getByTestId('waiting')).toContainText('Waiting for approval')

    const owner = await second(context)
    await enter(owner, '/users')
    const asking = owner.getByTestId('pending-row').filter({ hasText: 'Nell Vance' })
    await expect(asking).toContainText('nell@example.com')
    await expect(asking.getByTestId('pending-approve')).toHaveText('Approve as Family')
    await asking.getByTestId('pending-role').click()
    await owner.getByRole('dialog', { name: 'Role for Nell Vance' }).getByRole('button', { name: 'Friend', exact: true }).click()
    await expect(asking.getByTestId('pending-approve')).toHaveText('Approve as Friend')
    await asking.getByTestId('pending-approve').click()
    await expect(toast(owner)).toHaveText('Nell Vance can now sign in as Friend.')
    await expect(asking).toHaveCount(0)
    await expect(row(owner, 'Nell Vance')).toContainText('Google sign-in · Friend')

    await expect(page.getByTestId('home')).toBeVisible({ timeout: 12_000 })
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem('sh.session.v1')!).profile.name)).toBe('Nell Vance')

    // Deny asks first, then removes the request.
    const other = owner.getByTestId('pending-row').filter({ hasText: 'Luis Ferreira' })
    await other.getByTestId('pending-deny').click()
    const confirm = owner.getByRole('dialog', { name: 'Deny Luis Ferreira?' })
    await expect(confirm.getByRole('button', { name: 'Keep waiting', exact: true })).toHaveAttribute('data-focused', 'true')
    await confirm.getByRole('button', { name: 'Deny', exact: true }).click()
    await expect(other).toHaveCount(0)
    await owner.getByTestId('users-tab-activity').click()
    await expect(owner.getByTestId('audit-list')).toContainText('Ryan approved Nell Vance as Friend')
    await expect(owner.getByTestId('audit-list')).toContainText('Ryan declined Luis Ferreira')
  })

  test('a role change reaches the other person\'s app on their next request', async ({ page, context }) => {
    await enter(page, '/', { who: 'guest' })
    await expect(page.getByTestId('home')).toBeVisible()
    await expect(await more(page)).toHaveText(['Sports', 'Requests', 'Recordings', 'Settings'])
    await page.keyboard.press('Escape')

    const owner = await second(context)
    await enter(owner, '/users')
    await row(owner, 'Guest').click()
    const detail = owner.getByTestId('user-detail')
    await expect(detail.getByRole('heading', { name: 'Guest' })).toBeVisible()
    await detail.getByTestId('user-role').getByRole('button', { name: 'Manager', exact: true }).click()
    await expect(toast(owner)).toHaveText('Guest is now Manager.')
    await expect(detail.getByTestId('user-role').getByRole('button', { name: 'Manager', exact: true })).toHaveAttribute('aria-pressed', 'true')

    // Guest's token is now out of date: their next call is refused once, refreshed, and /auth/me is read again.
    await page.getByTestId('nav-live-tv').click()
    await expect(await more(page)).toHaveText(['Sports', 'Requests', 'Recordings', 'Users', 'Settings'])
    await page.getByTestId('more-menu').getByRole('link', { name: 'Users', exact: true }).click()
    await expect(page.getByTestId('users')).toBeVisible()

    // One permission taken away from this person only: marked as an override, with a way back to the role.
    const manage = detail.locator('[data-permission="users.manage"]')
    await expect(manage.getByTestId('toggle-note')).toHaveText('From role')
    await manage.getByRole('button').first().click()
    await expect(manage).toHaveAttribute('data-overridden', 'true')
    await expect(manage.getByTestId('toggle-note')).toHaveText('Overridden')
    await expect(page.getByTestId('users')).toHaveCount(0, { timeout: 25_000 })
    await expect(page.getByTestId('home')).toBeVisible()
    await manage.getByRole('button', { name: 'Reset Manage users to the role', exact: true }).click()
    await expect(manage).toHaveAttribute('data-overridden', 'false')
    await expect(manage.getByRole('button').first()).toHaveAttribute('aria-pressed', 'true')
  })

  test('user detail: limits, shared devices, PIN, Plex, devices, suspend and remove', async ({ page }) => {
    await enter(page, '/users/u5')
    const detail = page.getByTestId('user-detail')
    await expect(detail.getByRole('heading', { name: 'Mara Ellison' })).toBeVisible()
    await expect(detail).toContainText('mara@example.com · Google sign-in')
    // A granted permission and a raised stream limit show as overrides; the rest follows the Friend role.
    await expect(detail.locator('[data-permission="recordings.view"]')).toHaveAttribute('data-overridden', 'true')
    await expect(detail.locator('[data-permission="watch.live"]')).toHaveAttribute('data-overridden', 'false')
    const streams = detail.locator('[data-limit="max_streams"]')
    await expect(streams).toHaveAttribute('data-overridden', 'true')
    await expect(streams.getByRole('button', { name: '2', exact: true })).toHaveAttribute('aria-pressed', 'true')
    await streams.getByRole('button', { name: 'Reset streams at once to the role', exact: true }).click()
    await expect(streams).toHaveAttribute('data-overridden', 'false')
    await expect(streams.getByRole('button', { name: '1', exact: true })).toHaveAttribute('aria-pressed', 'true')
    // Ceilings come from the codes the server accepts.
    const movies = detail.locator('[data-limit="rating_ceiling_movie"]')
    await expect(movies.getByRole('button')).toHaveText(['Any rating', 'G', 'PG', 'PG-13', 'R', 'NC-17'])
    await movies.getByRole('button', { name: 'PG-13', exact: true }).click()
    await expect(movies).toHaveAttribute('data-overridden', 'true')
    await detail.locator('[data-limit="allow_unrated"]').getByRole('button', { name: 'Hidden', exact: true }).click()
    await expect(detail.locator('[data-limit="allow_unrated"]')).toHaveAttribute('data-overridden', 'true')
    await expect(streams).toHaveAttribute('data-overridden', 'false')

    const sharing = detail.getByTestId('user-sharing')
    const shared = sharing.getByRole('button', { name: /^Show on shared devices/ })
    await expect(shared).toHaveAttribute('aria-pressed', 'false')
    await shared.click()
    await expect(shared).toHaveAttribute('aria-pressed', 'true')
    await sharing.getByRole('button', { name: 'Reset shared devices to the role', exact: true }).click()
    await expect(shared).toHaveAttribute('aria-pressed', 'false')

    // Set a PIN (entered twice), then clear it.
    await page.getByTestId('user-pin-set').click()
    const pin = page.getByTestId('pin-dialog')
    await expect(pin.getByRole('heading', { name: 'New PIN for Mara Ellison' })).toBeVisible()
    await page.keyboard.type('2468')
    await expect(pin.getByTestId('pin-dots').locator('i')).toHaveCount(4)
    await expect(pin).not.toContainText('2468')
    await page.keyboard.press('Enter')
    await expect(pin).toContainText('Enter it once more to confirm.')
    await page.keyboard.type('2469')
    await page.keyboard.press('Enter')
    await expect(pin.getByTestId('pin-error')).toHaveText('Those two PINs are not the same. Start again.')
    for (const entry of ['2468', '2468']) { await page.keyboard.type(entry); await page.keyboard.press('Enter') }
    await expect(pin).toHaveCount(0)
    await expect(sharing).toContainText('Has a PIN')
    await page.getByTestId('user-pin-clear').click()
    await expect(sharing).toContainText('No PIN')

    // Plex: one linked person. Linking Mara takes it from the owner.
    await expect(sharing).toContainText('One person only')
    await page.getByTestId('user-plex').click()
    await expect(page.getByTestId('user-plex')).toHaveAttribute('aria-pressed', 'true')
    expect(await page.evaluate(async () => (await (await fetch('/api/v1/users', { headers: { authorization: 'Bearer mock-access-p1' } })).json() as Array<{ plex_account_linked: boolean }>).filter((u) => u.plex_account_linked).length)).toBe(1)

    const device = detail.getByTestId('device-row')
    await expect(device).toContainText('Browser on Mac')
    await device.getByTestId('device-shared').click()
    await expect(device.getByTestId('device-shared')).toHaveAttribute('aria-pressed', 'true')
    await device.getByTestId('device-revoke').click()
    await expect(detail.getByTestId('user-devices')).toContainText('Not signed in anywhere.')

    await page.getByTestId('user-suspend').click()
    const ask = page.getByRole('dialog', { name: 'Suspend Mara Ellison?' })
    await expect(ask.getByRole('button', { name: 'Cancel', exact: true })).toHaveAttribute('data-focused', 'true')
    await page.getByTestId('confirm-suspend').click()
    await expect(page.getByTestId('user-status')).toHaveText('Suspended')
    await page.getByTestId('user-reinstate').click()
    await expect(page.getByTestId('user-status')).toHaveText('Active')
    await page.getByTestId('user-remove').click()
    await page.getByTestId('confirm-remove').click()
    await expect(page).toHaveURL(/\/users$/)
    await expect(row(page, 'Mara Ellison')).toHaveCount(0)
  })

  test('the server\'s guards are shown in its own words', async ({ page }) => {
    await enter(page, '/users/u1')
    const detail = page.getByTestId('user-detail')
    await expect(detail.getByRole('heading', { name: 'Ryan' })).toContainText('You')
    await detail.getByTestId('user-role').getByRole('button', { name: 'Family', exact: true }).click()
    await expect(toast(page)).toHaveText('You cannot change your own role, permissions or limits')
    await expect(detail.getByTestId('user-role').getByRole('button', { name: 'Owner', exact: true })).toHaveAttribute('aria-pressed', 'true')
    await page.getByTestId('user-suspend').click()
    await page.getByTestId('confirm-suspend').click()
    await expect(toast(page)).toHaveText('The last active owner cannot be demoted, suspended or removed')
    await expect(page.getByTestId('user-status')).toHaveText('Active')
    await page.getByTestId('user-remove').click()
    await page.getByTestId('confirm-remove').click()
    await expect(toast(page)).toHaveText('The last active owner cannot be demoted, suspended or removed')
    await expect(page).toHaveURL(/\/users\/u1$/)
  })

  test('roles: Owner is read-only, built-ins are edited, custom roles are created and deleted', async ({ page }) => {
    await enter(page, '/users?tab=roles')
    const role = (name: string) => page.getByTestId('role-row').filter({ hasText: name })
    await expect(page.getByTestId('role-row')).toHaveCount(5)
    await expect(role('Kid')).toContainText('6 of 16 permissions · 1 stream at once · up to PG / TV-PG · unrated hidden')
    await page.getByTestId('role-edit-owner').click()
    const editor = page.getByTestId('role-editor')
    await expect(editor).toContainText('It cannot be edited or removed')
    await expect(editor.getByTestId('role-save')).toHaveCount(0)
    // All sixteen, each on, none of them a control.
    await expect(editor.locator('[data-permission] [data-readonly="true"][aria-pressed="true"]')).toHaveCount(16)
    await editor.locator('[data-permission="watch.adult"]').getByRole('button').click()
    await expect(editor.locator('[data-permission="watch.adult"]').getByRole('button')).toHaveAttribute('aria-pressed', 'true')
    await editor.getByRole('button', { name: 'Close', exact: true }).click()

    await page.getByTestId('role-edit-kid').click()
    await editor.locator('[data-permission="rooms.create"]').getByRole('button').click()
    await editor.locator('[data-limit="max_streams"]').getByRole('button', { name: '2', exact: true }).click()
    await editor.locator('[data-limit="rating_ceiling_movie"]').getByRole('button', { name: 'PG-13', exact: true }).click()
    await editor.getByTestId('role-save').click()
    await expect(editor).toHaveCount(0)
    await expect(role('Kid')).toContainText('7 of 16 permissions · 2 streams at once · up to PG-13 / TV-PG · unrated hidden')

    await page.getByTestId('role-new').click()
    await editor.getByLabel('Role name', { exact: true }).fill('Grandparents')
    await editor.locator('[data-permission="remote.access"]').getByRole('button').click()
    await editor.getByTestId('role-save').click()
    await expect(role('Grandparents')).toContainText('Custom')
    await expect(role('Grandparents')).toContainText('6 of 16 permissions')
    // A built-in has no Delete; a role people hold says why it stays.
    await page.getByTestId('role-edit-family').click()
    await expect(editor.getByRole('button', { name: 'Delete role', exact: true })).toHaveCount(0)
    await editor.getByRole('button', { name: 'Cancel', exact: true }).click()
    await page.getByRole('button', { name: 'Edit the Grandparents role', exact: true }).click()
    await editor.getByRole('button', { name: 'Delete role', exact: true }).click()
    const ask = page.getByRole('dialog', { name: 'Delete the Grandparents role?' })
    await expect(ask.getByRole('button', { name: 'Keep role', exact: true })).toHaveAttribute('data-focused', 'true')
    await page.getByTestId('confirm-role-delete').click()
    await expect(role('Grandparents')).toHaveCount(0)
    // A role someone holds stays, and the server says why.
    expect(await asOwner(page, 'PATCH', '/users/u2', { role_id: 'manager' })).toBe(200)
    await page.getByTestId('role-edit-manager').click()
    await editor.getByRole('button', { name: 'Delete role', exact: true }).click()
    await page.getByTestId('confirm-role-delete').click()
    await expect(toast(page)).toHaveText('Move the people who hold this role to another one first')
    await expect(role('Manager')).toHaveCount(1)
  })

  test('invites: create with role, expiry and uses; copy the link; it works once; revoke', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    await enter(page, '/users?tab=invites')
    const form = page.getByTestId('invite-form')
    await form.getByRole('button', { name: 'Kid', exact: true }).click()
    await form.getByRole('button', { name: '1 week', exact: true }).click()
    await page.getByTestId('invite-create').click()
    const link = await page.getByLabel('Invite link', { exact: true }).inputValue()
    expect(link).toMatch(/^http:\/\/localhost:\d+\/invite\/[A-Za-z0-9_-]{43}$/)
    await expect(page.getByTestId('invite-copy')).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('Enter')
    await expect(page.locator('.toast').last()).toHaveText('Link copied.')
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(link)
    const open = page.getByTestId('invite-row').filter({ hasText: 'Kid' })
    await expect(open).toContainText('Open')
    await expect(open).toContainText('Used 0 of 1')
    const made = await page.evaluate(async () => (await (await fetch('/api/v1/invites', { headers: { authorization: 'Bearer mock-access-p1' } })).json() as Array<{ role_id: string; max_uses: number; created_at: string; expires_at: string }>)[0]!)
    expect([made.role_id, made.max_uses, Math.round((Date.parse(made.expires_at) - Date.parse(made.created_at)) / 3600_000)]).toEqual(['kid', 1, 168])

    // Someone opens the link and signs in: active at once as Kid, and the invite is used up.
    const invited = await second(context)
    await invited.context().route('**/api/v1/auth/google/start*', (route) => route.fulfill({ status: 303, headers: { location: '/?google_complete=1' } }))
    await enter(invited, new URL(link).pathname, { google: `invite:${link.split('/').pop()}` })
    await invited.getByTestId('invite-accept').click()
    await expect(invited.getByTestId('home')).toBeVisible()
    await page.getByTestId('users-tab-people').click()
    await expect(row(page, 'Nell Vance')).toContainText('Google sign-in · Kid')
    await page.getByTestId('users-tab-invites').click()
    await expect(open).toContainText('Used up')

    const seeded = page.getByTestId('invite-row').filter({ hasText: 'Friend' }).filter({ hasText: 'Open' })
    await seeded.getByTestId('invite-revoke').click()
    await expect(page.locator('.toast').last()).toHaveText('Invite revoked.')
    await expect(page.getByTestId('invite-row').filter({ hasText: 'Revoked' })).toHaveCount(1)
    await expect(page.getByTestId('invite-create')).toHaveAttribute('data-focused', 'true')
  })

  test('activity lists who changed what and narrows to one person', async ({ page }) => {
    await enter(page, '/users?tab=activity')
    const log = page.getByTestId('audit-list')
    await expect(log.getByRole('listitem').or(log.getByRole('link'))).toHaveCount(5)
    await expect(log).toContainText('Ryan suspended Theo Okafor')
    await page.getByRole('button', { name: 'About: Everyone', exact: true }).click()
    await page.getByRole('dialog', { name: 'About' }).getByRole('button', { name: 'Theo Okafor', exact: true }).click()
    await expect(page).toHaveURL(/tab=activity&user=u6$/)
    await expect(log.getByRole('link')).toHaveText([/Ryan suspended Theo Okafor/])
    // An entry about a person opens that person.
    await log.getByRole('link').click()
    await expect(page.getByTestId('user-detail').getByRole('heading', { name: 'Theo Okafor' })).toBeVisible()
    await page.getByRole('button', { name: 'Activity about Theo Okafor', exact: true }).click()
    await expect(page).toHaveURL(/tab=activity&user=u6$/)
  })

  test('keyboard only: tabs, a role edit, a person and back, add a managed user', async ({ page }) => {
    await enter(page, '/users')
    await expect(page.getByTestId('users-tab-people')).toHaveAttribute('data-focused', 'true')
    await expect(page.getByTestId('user-row').first()).toBeVisible()

    // Roles: open Kid, switch one permission, save.
    await page.keyboard.press('ArrowRight')
    await page.keyboard.press('Enter')
    await expect(page.getByTestId('users-tab-roles')).toHaveAttribute('aria-pressed', 'true')
    await walk(page, 'ArrowDown', page.getByTestId('role-edit-kid'))
    await page.keyboard.press('Enter')
    const editor = page.getByTestId('role-editor')
    await expect(editor.locator('[data-permission="watch.library"]').getByRole('button')).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('ArrowRight')
    await page.keyboard.press('ArrowDown')
    await expect(focused(page)).toHaveAttribute('aria-label', 'Adult titles: off')
    await page.keyboard.press('Enter')
    await expect(focused(page)).toHaveAttribute('aria-label', 'Adult titles: on')
    await walk(page, 'ArrowDown', editor.getByTestId('role-save'))
    await page.keyboard.press('Enter')
    await expect(editor).toHaveCount(0)
    await expect(page.getByTestId('role-edit-kid')).toHaveAttribute('data-focused', 'true')
    await expect(page.getByTestId('role-row').filter({ hasText: 'Kid' })).toContainText('7 of 16 permissions')
    // Escape leaves a dialog without saving and puts the remote back on its opener.
    await page.keyboard.press('Enter')
    await expect(editor).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(editor).toHaveCount(0)
    await expect(page.getByTestId('role-edit-kid')).toHaveAttribute('data-focused', 'true')

    // People: open someone, come back to the same row.
    await walk(page, 'ArrowUp', page.getByTestId('users-tab-roles'))
    await page.keyboard.press('ArrowLeft')
    await page.keyboard.press('Enter')
    await walk(page, 'ArrowDown', row(page, 'Guest'))
    await page.keyboard.press('Enter')
    await expect(page).toHaveURL(/\/users\/u2$/)
    await expect(page.getByTestId('user-back')).toHaveAttribute('data-focused', 'true')
    await walk(page, 'ArrowDown', page.getByTestId('user-detail').locator('[data-permission="watch.library"]').getByRole('button').first())
    await walk(page, 'ArrowDown', page.getByRole('button', { name: 'Activity about Guest', exact: true }), 60)
    await page.keyboard.press('ArrowRight')
    await expect(page.getByTestId('user-suspend')).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('Backspace')
    await expect(page).toHaveURL(/\/users$/)
    await expect(row(page, 'Guest')).toHaveAttribute('data-focused', 'true')

    // Add a managed user: name, picture, role, PIN.
    await walk(page, 'ArrowUp', page.getByTestId('users-add'))
    await page.keyboard.press('Enter')
    const add = page.getByTestId('add-user')
    await expect(add.getByLabel('Name', { exact: true })).toBeFocused()
    await page.keyboard.type('Robin')
    await page.keyboard.press('ArrowDown')
    await expect(focused(page)).toHaveAttribute('aria-label', 'Picture: fox')
    await page.keyboard.press('ArrowRight')
    await page.keyboard.press('Enter')
    await expect(focused(page)).toHaveAttribute('aria-pressed', 'true')
    await walk(page, 'ArrowDown', add.getByRole('button', { name: 'Family', exact: true }))
    await page.keyboard.press('ArrowRight')
    await page.keyboard.press('Enter')
    await page.keyboard.press('ArrowDown')
    await expect(add.getByLabel('PIN, optional', { exact: true })).toBeFocused()
    await page.keyboard.type('24a68')
    await expect(add.getByLabel('PIN, optional', { exact: true })).toHaveValue('2468')
    await page.keyboard.press('ArrowDown')
    await expect(page.getByTestId('add-user-ok')).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('Enter')
    await expect(add).toHaveCount(0)
    await expect(page.getByTestId('users-add')).toHaveAttribute('data-focused', 'true')
    await expect(row(page, 'Robin')).toContainText('Managed sign-in · Kid')
    await expect(row(page, 'Robin').locator('[data-avatar="owl"]')).toBeVisible()
  })

  test.describe('on a phone', () => {
    test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true })
    test('nothing scrolls sideways on any tab or on a person', async ({ page }) => {
      await enter(page, '/users')
      for (const tab of ['people', 'roles', 'invites', 'activity']) {
        await page.getByTestId(`users-tab-${tab}`).tap()
        await expect(page.getByTestId(`users-tab-${tab}`)).toHaveAttribute('aria-pressed', 'true')
        expect(await fits(page), tab).toBe(true)
      }
      await page.getByTestId('users-tab-people').tap()
      await row(page, 'Mara Ellison').tap()
      await expect(page.getByTestId('user-detail').getByRole('heading', { name: 'Mara Ellison' })).toBeVisible()
      await page.getByTestId('user-remove').scrollIntoViewIfNeeded()
      expect(await fits(page)).toBe(true)
    })
  })
})

test.describe("who's watching", () => {
  const picker = (page: Page) => page.getByTestId('who-is-watching')
  const card = (page: Page, name: string) => picker(page).getByTestId('who-card').filter({ hasText: name })
  const pin = (page: Page) => page.getByTestId('pin-dialog')
  const key = (page: Page, digit: string) => pin(page).getByRole('button', { name: digit, exact: true })

  test('a shared device opens on the picker; a PIN switches to that user', async ({ page }) => {
    await enter(page, '/', { who: 'guest', shared: true })
    await expect(picker(page).getByRole('heading', { name: "Who's watching?" })).toBeVisible()
    await expect(picker(page)).toHaveAttribute('data-mandatory', 'true')
    // People whose role is offered on shared devices; a friend's is not. Lock badges mark the PINs.
    await expect(picker(page).getByTestId('who-card').locator('.who-name')).toHaveText(['Ryan', 'Guest', 'Kids', 'Priya'])
    await expect(card(page, 'Guest')).toHaveAttribute('data-focused', 'true')
    await expect(card(page, 'Guest')).toContainText('Watching now')
    for (const [name, locked] of [['Ryan', 0], ['Guest', 0], ['Kids', 1], ['Priya', 1]] as const) await expect(card(page, name).getByTestId('who-lock')).toHaveCount(locked)
    // At the start of the app there is no way round it.
    await expect(page.getByTestId('who-close')).toHaveCount(0)
    await page.keyboard.press('Escape')
    await page.keyboard.press('Backspace')
    await expect(picker(page)).toBeVisible()
    await expect(page.getByTestId('home')).toHaveCount(0)

    // Wider access without a PIN is refused, and says what to do.
    await page.keyboard.press('ArrowLeft')
    await expect(card(page, 'Ryan')).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('Enter')
    await expect(page.getByTestId('who-problem')).toContainText('Ryan has no PIN yet')

    // Kids has a PIN: wrong, then right, on the keypad.
    await card(page, 'Kids').click()
    await expect(pin(page).getByRole('heading', { name: 'PIN for Kids' })).toBeVisible()
    await expect(key(page, '1')).toHaveAttribute('data-focused', 'true')
    await expect(page.getByTestId('pin-ok')).toHaveAttribute('aria-disabled', 'true')
    for (const digit of '9999') await key(page, digit).click()
    await expect(pin(page).getByTestId('pin-dots').locator('i')).toHaveCount(4)
    await expect(pin(page).getByTestId('pin-dots')).toHaveAttribute('aria-label', '4 digits entered')
    await expect(pin(page)).not.toContainText('9999')
    await page.getByTestId('pin-ok').click()
    await expect(pin(page).getByTestId('pin-error')).toHaveText('That PIN is not right. Try again.')
    await expect(pin(page).getByTestId('pin-dots').locator('i')).toHaveCount(0)
    for (const digit of '12345') await key(page, digit).click()
    await pin(page).getByRole('button', { name: 'Erase last digit', exact: true }).click()
    await page.getByTestId('pin-ok').click()
    await expect(page.getByTestId('home')).toBeVisible()
    await expect(picker(page)).toHaveCount(0)
    await expect(page.getByTestId('nav-profile')).toHaveAttribute('aria-label', 'Profile Kids')
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem('sh.session.v1')!).access)).toBe('mock-access-p3')

    // The profile entry asks again; this time Back closes it.
    await page.getByTestId('nav-profile').click()
    await expect(picker(page)).toHaveAttribute('data-mandatory', 'false')
    await expect(card(page, 'Kids')).toHaveAttribute('data-focused', 'true')
    await expect(card(page, 'Kids')).toContainText('Watching now')
    await page.keyboard.press('Escape')
    await expect(picker(page)).toHaveCount(0)
    await expect(page.getByTestId('nav-profile')).toHaveAttribute('data-focused', 'true')

    // A new start of the app asks again.
    await page.reload()
    await expect(picker(page)).toHaveAttribute('data-mandatory', 'true')
    // Staying as Kids still takes the PIN, typed this time: digits only, Enter to switch.
    await expect(card(page, 'Kids')).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('Enter')
    await expect(pin(page)).toBeVisible()
    await page.keyboard.type('12x34')
    await expect(pin(page).getByTestId('pin-dots').locator('i')).toHaveCount(4)
    await page.keyboard.press('Enter')
    await expect(page.getByTestId('home')).toBeVisible()
  })

  test('five wrong PINs lock the keypad and count the wait down', async ({ page }) => {
    await enter(page, '/', { who: 'guest', shared: true })
    await card(page, 'Priya').click()
    for (let attempt = 1; attempt <= 4; attempt++) {
      await page.keyboard.type('0000')
      await page.keyboard.press('Enter')
      await expect(pin(page).getByTestId('pin-error')).toHaveText('That PIN is not right. Try again.')
      await expect(pin(page).getByTestId('pin-dots').locator('i')).toHaveCount(0)
    }
    await page.keyboard.type('0000')
    await page.keyboard.press('Enter')
    const locked = pin(page).getByTestId('pin-locked')
    await expect(locked).toHaveText(/^Too many wrong PINs\. Try again in 1[45]:\d\d\.$/)
    const before = await locked.textContent()
    await expect(locked).not.toHaveText(before!, { timeout: 4000 })
    await expect(key(page, '1')).toHaveAttribute('aria-disabled', 'true')
    await page.keyboard.type('4321')
    await expect(pin(page).getByTestId('pin-dots').locator('i')).toHaveCount(0)
    // The right PIN is refused too while the lock holds, and opening the dialog again shows the wait.
    await pin(page).getByRole('button', { name: 'Cancel', exact: true }).click()
    await card(page, 'Priya').click()
    await page.keyboard.type('4321')
    await page.keyboard.press('Enter')
    await expect(pin(page).getByTestId('pin-locked')).toContainText('Try again in 1')
    // Someone else can still be chosen.
    await pin(page).getByRole('button', { name: 'Cancel', exact: true }).click()
    await card(page, 'Guest').click()
    await expect(page.getByTestId('home')).toBeVisible()
  })

  test('a personal device has no picker', async ({ page }) => {
    await enter(page, '/', { who: 'guest' })
    await expect(page.getByTestId('home')).toBeVisible()
    await expect(picker(page)).toHaveCount(0)
    await page.getByTestId('nav-profile').click()
    await expect(page).toHaveURL(/\/settings\?tab=profiles$/)
  })

  test('a shared device on a server without the switch calls has no picker either', async ({ page }) => {
    const asked: number[] = []
    page.on('response', (r) => { if (r.url().endsWith('/api/v1/auth/switchable')) asked.push(r.status()) })
    await enter(page, '/', { who: 'guest', shared: true, mode: 'no-switch' })
    await expect(page.getByTestId('home')).toBeVisible()
    expect(new Set(asked)).toEqual(new Set([404]))
    await expect(picker(page)).toHaveCount(0)
    await page.getByTestId('nav-profile').click()
    await expect(page).toHaveURL(/\/settings\?tab=profiles$/)
  })

  test.describe('on a phone', () => {
    test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true })
    test('the picker and the keypad fit, and More reaches the picker', async ({ page }) => {
      await enter(page, '/', { who: 'guest', shared: true })
      await expect(picker(page)).toBeVisible()
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await card(page, 'Kids').tap()
      for (const digit of '1234') await key(page, digit).tap()
      const pad = (await pin(page).boundingBox())!
      expect(pad.x).toBeGreaterThanOrEqual(0)
      expect(pad.x + pad.width).toBeLessThanOrEqual(390)
      await page.getByTestId('pin-ok').tap()
      await expect(page.getByTestId('home')).toBeVisible()
      await page.getByTestId('nav-more').tap()
      await page.getByTestId('more-menu').getByTestId('nav-profile').tap()
      await expect(picker(page)).toHaveAttribute('data-mandatory', 'false')
      await page.getByTestId('who-close').tap()
      await expect(picker(page)).toHaveCount(0)
    })
  })
})

/** Record what the app sends, before the mock answers: [method, path with query, JSON body]. */
async function record(page: Page) {
  await page.addInitScript(() => {
    const sent: Array<[string, string, unknown]> = []
    ;(window as unknown as { sent: typeof sent }).sent = sent
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const request = new Request(input, init)
      const url = new URL(request.url)
      const text = request.method === 'GET' ? '' : await request.clone().text()
      sent.push([request.method, url.pathname.replace('/api/v1', '') + url.search, text ? JSON.parse(text) : null])
      return original(request)
    }
  })
  return (path: string) => page.evaluate((path) => (window as unknown as { sent: Array<[string, string, unknown]> }).sent.filter((s) => s[1].startsWith(path)), path)
}

test.describe('devices', () => {
  const device = (page: Page, name: string) => page.getByTestId('device-row').filter({ hasText: name })

  test('Settings lists your own devices: rename, revoke, and revoking this one signs out', async ({ page }) => {
    await enter(page, '/settings?tab=devices', { who: 'guest' })
    await expect(page.getByTestId('tab-devices')).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByTestId('device-row')).toHaveCount(2)
    await expect(device(page, 'Browser on Windows')).toContainText('This device')
    await expect(device(page, 'Browser on Windows')).toContainText('Browser · Last seen just now')
    await expect(device(page, 'Kitchen tablet')).toContainText('Last seen 3 h ago')
    // Marking a device as shared is for those who manage users.
    await expect(page.getByTestId('device-shared')).toHaveCount(0)

    await device(page, 'Kitchen tablet').getByTestId('device-rename').click()
    const name = page.getByLabel('Device name', { exact: true })
    await expect(name).toHaveValue('Kitchen tablet')
    await name.fill('Hall tablet')
    await page.keyboard.press('Enter')
    await expect(device(page, 'Hall tablet')).toBeVisible()
    await device(page, 'Hall tablet').getByTestId('device-revoke').click()
    await expect(page.getByTestId('device-row')).toHaveCount(1)
    await device(page, 'Browser on Windows').getByTestId('device-revoke').click()
    await expect(page.getByTestId('profile-picker')).toBeVisible()
    expect(await page.evaluate(() => localStorage.getItem('sh.session.v1'))).toBeNull()
  })

  test('an owner marks this browser as a shared household device; it then opens on the picker', async ({ page }) => {
    await enter(page, '/settings?tab=devices')
    await expect(device(page, 'Living room TV')).toContainText('TV · Last seen 35 min ago')
    await expect(device(page, 'Living room TV').getByTestId('device-shared')).toHaveAttribute('aria-pressed', 'true')
    const here = device(page, 'This device')
    await expect(here.getByTestId('device-shared')).toHaveAttribute('aria-pressed', 'false')
    await here.getByTestId('device-shared').click()
    await expect(here.getByTestId('device-shared')).toHaveAttribute('aria-pressed', 'true')
    await expect(here).toContainText('Shared')
    await page.reload()
    await expect(page.getByTestId('who-is-watching')).toHaveAttribute('data-mandatory', 'true')
  })

  test('older servers: no rename or shared switch once the call answers 404, no Devices tab without users', async ({ page, context }) => {
    await enter(page, '/settings?tab=devices', { who: 'owner', mode: 'no-switch' })
    await device(page, 'Living room TV').getByTestId('device-rename').click()
    await page.getByLabel('Device name', { exact: true }).fill('Den TV')
    await page.getByTestId('device-rename-save').click()
    await expect(page.locator('.toast').last()).toHaveText('This server cannot rename devices yet.')
    await expect(page.getByTestId('device-rename')).toHaveCount(0)
    await expect(page.getByTestId('device-shared')).toHaveCount(0)
    await expect(page.getByTestId('device-revoke')).toHaveCount(3)

    const legacy = await second(context)
    await enter(legacy, '/settings', { who: 'owner', mode: 'legacy' })
    await expect(legacy.getByTestId('tab-about')).toBeVisible()
    await expect(legacy.getByTestId('tab-devices')).toHaveCount(0)
  })

  test('pairing a TV: the shared-device choice is the user manager\'s and is sent as shared_device', async ({ page, context }) => {
    const sent = await record(page)
    await enter(page, '/pair')
    await page.getByLabel('TV code', { exact: true }).fill('ABC123')
    await expect(page.getByTestId('pair-shared-toggle')).toHaveAttribute('aria-pressed', 'false')
    await page.getByTestId('pair-shared-toggle').click()
    await expect(page.getByTestId('pair-shared-toggle')).toHaveAttribute('aria-pressed', 'true')
    await page.getByTestId('pair-approve').click()
    await expect(page.getByTestId('pair-done')).toBeVisible()
    await expect(page.getByTestId('pair-shared')).toBeVisible()
    expect(await sent('/auth/device/approve')).toEqual([['POST', '/auth/device/approve', { user_code: 'ABC123', profile_id: 'p1', shared_device: true }]])

    const guest = await second(context)
    const plain = await record(guest)
    await enter(guest, '/pair', { who: 'guest' })
    await guest.getByLabel('TV code', { exact: true }).fill('ABC123')
    await expect(guest.getByTestId('pair-shared-toggle')).toHaveCount(0)
    await guest.getByTestId('pair-approve').click()
    await expect(guest.getByTestId('pair-done')).toBeVisible()
    expect(await plain('/auth/device/approve')).toEqual([['POST', '/auth/device/approve', { user_code: 'ABC123', profile_id: 'p2' }]])
  })
})

test.describe('adult titles', () => {
  const shelf = (page: Page) => page.locator('[data-row="shelf:After Dark"]')
  const filters = async (page: Page) => {
    await page.getByTestId('browse-filter').click()
    await expect(page.getByTestId('browse-filters')).toBeVisible()
  }

  test('someone without the permission is never offered them', async ({ page }) => {
    const sent = await record(page)
    await enter(page, '/browse/movies', { who: 'guest' })
    await expect(page.getByTestId('row').first()).toBeVisible()
    await filters(page)
    await expect(page.getByTestId('adult-titles')).toHaveCount(0)
    await expect(page.getByText(/adult/i)).toHaveCount(0)
    expect((await sent('/browse/movies/shelves')).every((s) => !s[1].includes('include_adult'))).toBe(true)
  })

  test('the holder unlocks with their own PIN, choosing one first if they have none, and can lock again', async ({ page }) => {
    const sent = await record(page)
    await enter(page, '/browse/movies')
    await expect(page.getByTestId('row').first()).toBeVisible()
    await expect(shelf(page)).toHaveCount(0)
    await filters(page)
    await page.getByTestId('adult-unlock').click()
    const pin = page.getByTestId('pin-dialog')
    // No PIN yet: choose one (twice), which also unlocks.
    await expect(pin.getByRole('heading', { name: 'Choose a PIN first' })).toBeVisible()
    for (const entry of ['2468', '2468']) { await page.keyboard.type(entry); await page.keyboard.press('Enter') }
    await expect(pin).toHaveCount(0)
    await expect(shelf(page)).toBeVisible()
    expect(await sent('/users/u1/pin')).toEqual([['PUT', '/users/u1/pin', { pin: '2468' }]])
    expect(await sent('/auth/adult-unlock')).toEqual([['POST', '/auth/adult-unlock', { pin: '2468' }]])
    expect((await sent('/browse/movies/shelves')).at(-1)![1]).toContain('include_adult=true')

    // Lock is always in reach while they show.
    await expect(page.getByTestId('adult-unlocked')).toContainText('Adult titles are shown on this device.')
    await page.getByTestId('adult-lock').click()
    await expect(shelf(page)).toHaveCount(0)
    await expect(page.getByTestId('adult-unlocked')).toHaveCount(0)

    // Now there is a PIN: it is asked for, a wrong one is refused, the right one unlocks.
    await filters(page)
    await page.getByTestId('adult-unlock').click()
    await expect(pin.getByRole('heading', { name: 'Your PIN' })).toBeVisible()
    await page.keyboard.type('1111')
    await page.keyboard.press('Enter')
    await expect(pin.getByTestId('pin-error')).toHaveText('That PIN is not right. Try again.')
    await page.keyboard.type('2468')
    await page.keyboard.press('Enter')
    await expect(shelf(page)).toBeVisible()
    // Unlocked for the session: still so after a reload, and the panel offers Lock instead.
    await page.reload()
    await expect(shelf(page)).toBeVisible()
    await filters(page)
    await expect(page.getByTestId('adult-titles').getByRole('button', { name: 'Lock adult titles', exact: true })).toBeVisible()
    await expect(page.getByTestId('adult-unlock')).toHaveCount(0)
  })
})
