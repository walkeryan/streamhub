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
    await expect(await more(page)).toHaveText(['Sports', 'Requests', 'Settings'])
    const owner = await second(context)
    await enter(owner, '/')
    await expect(await more(owner)).toHaveText(['Sports', 'Requests', 'Sources', 'Settings'])
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
