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
