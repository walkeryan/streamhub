import { expect, test, type BrowserContext, type Page } from '@playwright/test'

// Three ways to sign in from anywhere (Google, Plex, a username and password), each shown only when
// the server has it on; invitations accepted any of the three ways; linking and unlinking them. The
// mock keeps its household in localStorage, so two tabs of one context are two browsers looking at
// the same server.
const profile = (id: string, name: string, is_admin = false) => ({ id, name, avatar: null, is_admin, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' })
const OWNER = profile('p1', 'Ryan', true)
const OPEN_INVITE = 'inv-open'.padEnd(43, 'x'), USED_INVITE = 'inv-used'.padEnd(43, 'x')
const GOOD = 'correct horse battery'
interface Setup { signedIn?: boolean; ways?: string; plex?: string }

/** Prepare a tab. `ways` is what the server has switched on ("remote": this browser is away from the home network). */
async function prepare(page: Page, { signedIn, ways, plex }: Setup = {}) {
  await page.addInitScript(({ session, ways, plex }) => {
    sessionStorage.setItem('mock.accepted', JSON.stringify(Array.from({ length: 60 }, (_, i) => `p${i + 1}`)))
    if (ways !== undefined) sessionStorage.setItem('mock.signin', ways)
    if (plex) sessionStorage.setItem('mock.plex', plex)
    if (sessionStorage.getItem('spec.tab')) return
    sessionStorage.setItem('spec.tab', '1')
    if (session) localStorage.setItem('sh.session.v1', JSON.stringify({ access: `mock-access-${session.id}`, refresh: `mock-refresh-${session.id}`, profile: session }))
    else localStorage.removeItem('sh.session.v1')
  }, { session: signedIn ? OWNER : null, ways, plex })
}
async function enter(page: Page, path: string, setup: Setup = {}) {
  await prepare(page, setup)
  await page.goto(path)
}
const button = (page: Page, name: string) => page.getByRole('button', { name, exact: true })
const noSidewaysScroll = async (page: Page) => expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0)
const linkIn = async (page: Page, label: string) => new URL(await page.getByLabel(label, { exact: true }).inputValue()).pathname
/** Owner: Users → Add person → the setup link's path. */
async function addPerson(owner: Page, name: string, role = 'Friend'): Promise<string> {
  await owner.getByTestId('users-add-person').click()
  const dialog = owner.getByTestId('add-person')
  await dialog.getByLabel('Name', { exact: true }).fill(name)
  await dialog.getByRole('button', { name: role, exact: true }).click()
  await owner.getByTestId('add-person-ok').click()
  await expect(owner.getByTestId('person-made')).toContainText(`Send this link to ${name}`)
  const path = await linkIn(owner, 'Setup link')
  expect(path).toMatch(/^\/invite\/[A-Za-z0-9_-]{43}$/)
  return path
}
async function choosePassword(page: Page, username: string | null, password = GOOD) {
  if (username !== null) await page.getByLabel('Username', { exact: true }).fill(username)
  await page.getByLabel('Password', { exact: true }).fill(password)
  await page.getByLabel('Password again', { exact: true }).fill(password)
  await page.getByTestId('invite-save').click()
}
const second = (context: BrowserContext) => context.newPage()

test.describe('the sign-in screen', () => {
  test('offers each way only when the server has it on, and no profile tiles away from home', async ({ page, context }) => {
    await enter(page, '/', { ways: 'google,plex,password,remote' })
    const screen = page.getByTestId('sign-in')
    await expect(screen.getByRole('heading', { name: 'Welcome to StreamHub' })).toBeVisible()
    await expect(screen.getByRole('group', { name: 'Ways to sign in' }).getByRole('button')).toHaveText(['Continue with Google', 'Continue with Plex', 'Username and password'])
    await expect(page.getByTestId('profile-picker')).toHaveCount(0)
    // Keyboard and remote: the first way has the cursor, Down walks the three.
    await expect(button(page, 'Continue with Google')).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('ArrowDown')
    await expect(button(page, 'Continue with Plex')).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('ArrowDown')
    await expect(button(page, 'Username and password')).toHaveAttribute('data-focused', 'true')

    const two = await second(context)
    await enter(two, '/', { ways: 'plex,password,remote' })
    await expect(two.getByRole('group', { name: 'Ways to sign in' }).getByRole('button')).toHaveText(['Continue with Plex', 'Username and password'])
    await expect(button(two, 'Continue with Plex')).toHaveAttribute('data-focused', 'true')

    // On the home network the household's profiles are still offered, under the ways in.
    const home = await second(context)
    await enter(home, '/', { ways: 'password' })
    await expect(home.getByRole('group', { name: 'Ways to sign in' }).getByRole('button')).toHaveText(['Username and password'])
    await expect(home.getByTestId('profile-picker')).toBeVisible()

    // Nothing on, and not at home: said plainly rather than an empty page.
    const none = await second(context)
    await enter(none, '/', { ways: 'remote' })
    await expect(none.getByRole('alert')).toContainText('No way of signing in from here is switched on')
    await expect(none.getByTestId('profile-picker')).toHaveCount(0)
  })

  test('a server from before these ways shows what it always did', async ({ page }) => {
    await enter(page, '/')
    await expect(page.getByTestId('profile-picker')).toBeVisible()
    await expect(page.getByTestId('sign-in')).toHaveCount(0)
  })

  test('fits a phone without sideways scrolling', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await enter(page, '/', { ways: 'google,plex,password,remote' })
    await expect(button(page, 'Continue with Plex')).toBeVisible()
    await noSidewaysScroll(page)
    await button(page, 'Username and password').click()
    await expect(page.getByLabel('Username', { exact: true })).toBeVisible()
    await noSidewaysScroll(page)
    await enter(page, `/invite/${OPEN_INVITE}`, { ways: 'google,plex,password,remote' })
    await page.getByTestId('invite-password').click()
    await expect(page.getByTestId('invite-form')).toBeVisible()
    await noSidewaysScroll(page)
  })
})

test.describe('Continue with Plex', () => {
  test('someone new waits for approval, and is let in once approved', async ({ page }) => {
    await enter(page, '/', { ways: 'plex,remote' })
    await button(page, 'Continue with Plex').click()
    // Plex sent the browser back; the state it carried is gone from the address bar.
    await expect(page.getByTestId('waiting')).toBeVisible()
    await expect(page.getByTestId('waiting-who')).toContainText('Plex Pal')
    expect(new URL(page.url()).search).toBe('')
    const id = await page.evaluate(() => (JSON.parse(localStorage.getItem('mock.people.v1')!) as { users: Array<{ id: string; display_name: string }> }).users.find((u) => u.display_name === 'Plex Pal')!.id)
    const approved = await page.evaluate(async (id) => (await fetch(`/api/v1/users/${id}/approve`, { method: 'POST', headers: { authorization: 'Bearer mock-access-p1', 'content-type': 'application/json' }, body: JSON.stringify({ role_id: 'friend' }) })).status, id)
    expect(approved).toBe(200)
    await page.getByTestId('waiting-check').click()
    await expect(page.getByTestId('home')).toBeVisible()
  })

  test('invite-only refuses a stranger and says why; a replayed return is refused', async ({ page }) => {
    await enter(page, '/', { ways: 'plex,remote,invite_only' })
    await expect(page.getByTestId('sign-in')).toContainText('You need an invite link')
    await button(page, 'Continue with Plex').click()
    await expect(page.getByTestId('signin-error')).toContainText('New people need an invitation here')
    // A state nobody started (or one already used) signs nobody in.
    await page.goto('/?plex=made-up-state')
    await expect(page.getByTestId('signin-error')).toContainText('was not finished or took too long')
    await expect(page.getByTestId('home')).toHaveCount(0)
  })
})

test.describe('username and password', () => {
  test('add a person, accept the setup link with a password, then sign in with it', async ({ page, context }) => {
    await enter(page, '/users', { signedIn: true, ways: 'password' })
    // Reachable without a mouse: it sits beside "Add managed user".
    await page.getByTestId('users-add').hover()
    await page.keyboard.press('ArrowLeft')
    await expect(page.getByTestId('users-add-person')).toHaveAttribute('data-focused', 'true')
    const path = await addPerson(page, 'Gran')
    await page.getByTestId('add-person-done').click()
    // Until it is used she is an invitation with her name on it, not a user.
    await page.getByTestId('users-tab-invites').click()
    await expect(page.getByTestId('invite-row').first()).toContainText('Gran · Friend')

    const gran = await second(context)
    await enter(gran, path, { ways: 'password,remote' })
    await expect(gran.getByRole('heading', { name: 'Gran, you are invited to StreamHub' })).toBeVisible()
    await expect(gran.getByRole('group', { name: 'Ways to accept' }).getByRole('button')).toHaveText(['Choose a username and password'])
    await gran.getByTestId('invite-password').click()
    // What the server would refuse is said before it is asked.
    await choosePassword(gran, 'Gran Smith')
    await expect(gran.getByTestId('invite-error')).toContainText('3 to 32 letters, digits')
    await choosePassword(gran, 'gran', 'short')
    await expect(gran.getByTestId('invite-error')).toContainText('at least 10 characters')
    await choosePassword(gran, 'gran', 'gran-is-my-name')
    await expect(gran.getByTestId('invite-error')).toContainText('cannot contain the username')
    await choosePassword(gran, 'gran')
    await expect(gran.getByTestId('home')).toBeVisible()

    await page.getByTestId('users-tab-people').click()
    await expect(page.getByTestId('user-row').filter({ hasText: 'Gran' })).toContainText('Password sign-in · Friend')
    // The link worked once.
    const again = await second(context)
    await enter(again, path, { ways: 'password,remote' })
    await expect(again.getByTestId('invite-dead')).toBeVisible()

    const later = await second(context)
    await enter(later, '/', { ways: 'password,remote' })
    await button(later, 'Username and password').click()
    await expect(later.getByLabel('Username', { exact: true })).toBeFocused()
    await later.getByLabel('Username', { exact: true }).fill('gran')
    await later.getByLabel('Password', { exact: true }).fill('not her password')
    await later.keyboard.press('Enter')
    await expect(later.getByTestId('password-error')).toHaveText('That username or password is not right.')
    // An unknown name reads exactly the same.
    await later.getByLabel('Username', { exact: true }).fill('nobody')
    await later.getByLabel('Password', { exact: true }).fill('not her password')
    await later.getByTestId('password-submit').click()
    await expect(later.getByTestId('password-error')).toHaveText('That username or password is not right.')
    await later.getByLabel('Username', { exact: true }).fill('GRAN')
    await later.getByLabel('Password', { exact: true }).fill(GOOD)
    await later.getByTestId('password-submit').click()
    await expect(later.getByTestId('home')).toBeVisible()
  })

  test('too many wrong passwords are answered with how long to wait', async ({ page }) => {
    await enter(page, '/', { ways: 'password,remote' })
    await button(page, 'Username and password').click()
    for (let attempt = 0; attempt < 6; attempt++) {
      await page.getByLabel('Username', { exact: true }).fill('someone')
      await page.getByLabel('Password', { exact: true }).fill(`wrong guess ${attempt}`)
      await page.getByTestId('password-submit').click()
      await expect(page.getByTestId('password-error')).toBeVisible()
      await expect(page.getByLabel('Password', { exact: true })).toHaveValue('')
    }
    await expect(page.getByTestId('password-error')).toContainText('Too many attempts. Try again in')
  })
})

test.describe('an invitation', () => {
  test('offers the three ways to accept, and Plex carries it', async ({ page }) => {
    await enter(page, `/invite/${OPEN_INVITE}`, { ways: 'google,plex,password,remote' })
    const landing = page.getByTestId('invite')
    await expect(landing.getByRole('heading', { name: 'You are invited to StreamHub' })).toBeVisible()
    await expect(landing.getByRole('group', { name: 'Ways to accept' }).getByRole('button')).toHaveText(['Accept with Google', 'Accept with Plex', 'Choose a username and password'])
    await expect(button(page, 'Accept with Google')).toHaveAttribute('data-focused', 'true')
    await page.getByTestId('invite-plex').click()
    // Active at once, with the invite's role: no approval step.
    await expect(page.getByTestId('home')).toBeVisible()
    const me = await page.evaluate(async () => (await fetch('/api/v1/auth/me', { headers: { authorization: `Bearer ${JSON.parse(localStorage.getItem('sh.session.v1')!).access}` } })).json() as Promise<{ status: string; role: { id: string }; user: { sign_in_methods: string[] } }>)
    expect([me.status, me.role.id, me.user.sign_in_methods]).toEqual(['active', 'friend', ['plex']])
  })

  test('a link that can no longer be used says so before anyone signs in', async ({ page }) => {
    await enter(page, `/invite/${USED_INVITE}`, { ways: 'google,plex,password,remote' })
    await expect(page.getByRole('heading', { name: 'This link can no longer be used' })).toBeVisible()
    await expect(page.getByRole('group', { name: 'Ways to accept' })).toHaveCount(0)
    await expect(button(page, 'Go to sign-in')).toHaveAttribute('data-focused', 'true')
  })
})

test.describe('sign-in methods', () => {
  test('Settings: link Plex and a password to oneself, change the password, remove one; never the last', async ({ page }) => {
    await enter(page, '/settings', { signedIn: true, ways: 'google,plex,password', plex: '42:ryanplex' })
    const methods = page.getByTestId('signin-methods')
    await expect(methods.getByTestId('signin-method-google')).toContainText('owner@example.com · Your only way to sign in')
    await expect(page.getByTestId('signin-unlink-google')).toHaveAttribute('aria-disabled', 'true')

    await page.getByTestId('signin-link-plex').click()
    await expect(methods.getByTestId('signin-method-plex')).toContainText('ryanplex')
    await expect(methods.getByTestId('signin-method-plex')).toHaveAttribute('data-linked', 'true')
    expect(new URL(page.url()).search).not.toContain('plex=')

    await page.getByTestId('signin-link-password').click()
    const dialog = page.getByTestId('password-dialog')
    await dialog.getByLabel('Username', { exact: true }).fill('ryan')
    await dialog.getByLabel('New password', { exact: true }).fill('ryan-secret-phrase')
    await dialog.getByLabel('New password again', { exact: true }).fill('ryan-secret-phrase')
    await page.getByTestId('password-save').click()
    await expect(page.getByTestId('password-dialog-error')).toContainText('cannot contain the username')
    await dialog.getByLabel('New password', { exact: true }).fill(GOOD)
    await dialog.getByLabel('New password again', { exact: true }).fill(GOOD)
    await page.getByTestId('password-save').click()
    await expect(methods.getByTestId('signin-method-password')).toHaveAttribute('data-linked', 'true')
    await expect(methods.getByTestId('signin-method-password')).toContainText('ryan')

    // Changing it needs the current one.
    await page.getByTestId('signin-change-password').click()
    await dialog.getByLabel('Current password', { exact: true }).fill('not the current one')
    await dialog.getByLabel('New password', { exact: true }).fill('a different phrase')
    await dialog.getByLabel('New password again', { exact: true }).fill('a different phrase')
    await page.getByTestId('password-save').click()
    await expect(page.getByTestId('password-dialog-error')).toHaveText('That is not your current password.')
    await dialog.getByLabel('Current password', { exact: true }).fill(GOOD)
    await page.getByTestId('password-save').click()
    await expect(dialog).toHaveCount(0)

    // Removing asks first, with the cursor on the safe answer.
    await page.getByTestId('signin-unlink-plex').click()
    await expect(button(page, 'Keep it')).toHaveAttribute('data-focused', 'true')
    await page.getByTestId('signin-unlink-ok').click()
    await expect(methods.getByTestId('signin-method-plex')).toHaveAttribute('data-linked', 'false')
    await page.getByTestId('signin-unlink-google').click()
    await page.getByTestId('signin-unlink-ok').click()
    await expect(methods.getByTestId('signin-method-google')).toHaveAttribute('data-linked', 'false')
    await expect(page.getByTestId('signin-unlink-password')).toHaveAttribute('aria-disabled', 'true')
    await expect(page.getByTestId('home')).toHaveCount(0)
  })

  test('a Plex account that already signs in as someone else is refused', async ({ page }) => {
    // Mara holds Plex account 77; the owner tries to link the same one.
    await enter(page, '/settings', { signedIn: true, ways: 'google,plex,password', plex: '77:maraplex' })
    await expect(page.getByTestId('signin-methods')).toBeVisible()
    await page.evaluate(() => {
      const db = JSON.parse(localStorage.getItem('mock.people.v1')!) as { users: Array<Record<string, unknown>> }
      Object.assign(db.users.find((u) => u.id === 'u5')!, { plex_account: '77', plex_username: 'maraplex' })
      localStorage.setItem('mock.people.v1', JSON.stringify(db))
    })
    await page.getByTestId('signin-link-plex').click()
    await expect(page.getByText('That account already signs in as someone else here.')).toBeVisible()
    await expect(page.getByTestId('signin-method-plex')).toHaveAttribute('data-linked', 'false')
  })

  test('Users: each person\'s methods, unlinking one, and a reset that hands out a setup link', async ({ page, context }) => {
    await enter(page, '/users/u5', { signedIn: true, ways: 'google,plex,password' })
    const section = page.getByTestId('user-signin')
    await expect(section.getByTestId('user-method-google')).toContainText('mara@example.com · Their only way to sign in')
    await expect(page.getByTestId('user-unlink-google')).toHaveAttribute('aria-disabled', 'true')

    // No password yet: the owner makes a setup link and passes it on.
    await page.getByTestId('user-reset').click()
    await expect(button(page, 'Cancel')).toHaveAttribute('data-focused', 'true')
    await page.getByTestId('confirm-reset').click()
    await expect(page.getByTestId('setup-link')).toContainText('Send this link to Mara Ellison')
    const path = await linkIn(page, 'Setup link')
    await page.getByTestId('setup-link').getByRole('button', { name: 'Done', exact: true }).click()

    const mara = await second(context)
    await enter(mara, path, { ways: 'google,plex,password,remote' })
    // A setup link sets one person's password: there is nothing to accept with Google or Plex.
    await expect(mara.getByRole('heading', { name: 'Choose your password' })).toBeVisible()
    await expect(mara.getByRole('group', { name: 'Ways to accept' })).toHaveCount(0)
    await choosePassword(mara, 'mara')
    await expect(mara.getByTestId('home')).toBeVisible()

    // Looked at again from the list (a reload would pick up whichever tab signed in last: one browser, one store).
    await page.getByTestId('user-back').click()
    await page.getByTestId('user-row').filter({ hasText: 'Mara Ellison' }).click()
    await expect(section.getByTestId('user-method-password')).toContainText('mara')
    await expect(page.getByTestId('user-detail')).toContainText('Google + Password sign-in')
    // Now there are two, the owner can take one away; it asks first.
    await page.getByTestId('user-unlink-google').click()
    await page.getByTestId('confirm-unlink').click()
    await expect(section.getByTestId('user-method-google')).toHaveCount(0)
    await expect(page.getByTestId('user-unlink-password')).toHaveAttribute('aria-disabled', 'true')

    // A reset of a password that exists: her link shows the username she keeps.
    await page.getByTestId('user-reset').click()
    await expect(page.getByRole('dialog')).toContainText('stops working at once')
    await page.getByTestId('confirm-reset').click()
    const reset = await linkIn(page, 'Setup link')
    const again = await second(context)
    await enter(again, reset, { ways: 'password,remote' })
    await expect(again.getByTestId('invite-username')).toContainText('mara')
    await choosePassword(again, null, 'a brand new phrase')
    await expect(again.getByTestId('home')).toBeVisible()
    // What was done is on the record.
    await page.getByTestId('setup-link').getByRole('button', { name: 'Done', exact: true }).click()
    await button(page, 'Activity about Mara Ellison').click()
    await expect(page.getByTestId('audit-list')).toContainText("reset Mara Ellison's password and made a setup link")
    await expect(page.getByTestId('audit-list')).toContainText('removed Google sign-in from Mara Ellison')
  })
})
