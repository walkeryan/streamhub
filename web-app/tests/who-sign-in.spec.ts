import { expect, test, type Page } from '@playwright/test'

// Each person signs in on "Who's watching?" one way of their choosing, and is asked for it that way:
// a number PIN on the keypad, an arrow pattern pressed on the remote's D-pad (which reaches the web
// app as the arrow keys), or a typed password.

const profile = (id: string, name: string, is_admin = false) => ({ id, name, avatar: null, is_admin, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' })
const WHO = { owner: profile('p1', 'Owner', true), guest: profile('p2', 'Guest') }

async function enter(page: Page, path: string, who: keyof typeof WHO, shared = false) {
  await page.addInitScript(({ session, shared }) => {
    sessionStorage.setItem('mock.accepted', JSON.stringify(Array.from({ length: 40 }, (_, i) => `p${i + 1}`)))
    if (shared) localStorage.setItem('mock.device', 'shared')
    if (sessionStorage.getItem('spec.tab')) return
    sessionStorage.setItem('spec.tab', '1')
    localStorage.setItem('sh.session.v1', JSON.stringify({ access: `mock-access-${session.id}`, refresh: `mock-refresh-${session.id}`, profile: session }))
  }, { session: WHO[who], shared })
  await page.goto(path)
}
/** Something the owner does on another device: straight to the API. */
const asOwner = (page: Page, method: string, path: string, body?: unknown) => page.evaluate(async ({ method, path, body }) => {
  const response = await fetch(`/api/v1${path}`, { method, headers: { authorization: 'Bearer mock-access-p1', 'content-type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  return response.status
}, { method, path, body })
const focusKey = (page: Page) => page.locator('[data-focus-key][data-focused="true"]').last().getAttribute('data-focus-key')

test.describe("Who's watching asks the way each person chose", () => {
  test.beforeEach(async ({ page }) => {
    await enter(page, '/', 'guest', true)
    await expect(page.getByTestId('who-is-watching')).toBeVisible()
  })

  test('an arrow pattern: dots fill with the arrow keys and the last one switches, with no OK', async ({ page }) => {
    // Up, Up, Down, Down: the keypad digits 2, 2, 8, 8.
    expect(await asOwner(page, 'PUT', '/users/u4/pin', { pin: '2288', kind: 'arrows' })).toBeLessThan(300)
    await page.reload()
    const card = page.getByTestId('who-card').filter({ hasText: 'Priya' })
    await expect(card).toHaveAttribute('aria-label', /arrow pattern needed/)
    await card.click()
    const dialog = page.getByTestId('arrow-ask-dialog')
    await expect(dialog.getByRole('heading', { name: 'Arrow pattern for Priya' })).toBeVisible()
    await expect(page.getByTestId('pin-dialog')).toHaveCount(0)
    const dots = dialog.getByTestId('arrow-dots')
    await expect(dots.locator('i')).toHaveCount(4)
    await expect(dots).toHaveAttribute('data-entered', '0')
    await expect.poll(() => focusKey(page)).toBe('arrowask:cancel')

    // The arrows fill dots and never move focus, inside the dialog or to the cards behind it.
    await page.keyboard.press('ArrowUp')
    await page.keyboard.press('ArrowLeft')
    await expect(dots).toHaveAttribute('data-entered', '2')
    expect(await focusKey(page)).toBe('arrowask:cancel')
    // Which arrows were pressed is not shown: the dots say how many, never which.
    await expect(dots).toHaveText('')
    await expect(dots).toHaveAttribute('aria-label', '2 of 4 arrows entered')
    // OK starts again; it never sends.
    await page.keyboard.press('Enter')
    await expect(dots).toHaveAttribute('data-entered', '0')
    await expect(dialog).toBeVisible()

    // A wrong pattern is sent on its last arrow, refused, and the dots empty.
    for (const key of ['ArrowUp', 'ArrowUp', 'ArrowDown', 'ArrowUp']) await page.keyboard.press(key)
    await expect(dialog.getByTestId('arrow-error')).toHaveText('That pattern is not right. Try again.')
    await expect(dots).toHaveAttribute('data-entered', '0')
    expect(await focusKey(page)).toBe('arrowask:cancel')

    // The right one switches as soon as the last dot fills.
    for (const key of ['ArrowUp', 'ArrowUp', 'ArrowDown', 'ArrowDown']) await page.keyboard.press(key)
    await expect(page.getByTestId('home')).toBeVisible()
    await expect(page.getByTestId('nav-profile')).toHaveAttribute('aria-label', 'Profile Priya')
  })

  test('an arrow pattern can be tapped on the on-screen arrows, and Escape closes it', async ({ page }) => {
    expect(await asOwner(page, 'PUT', '/users/u4/pin', { pin: '468246', kind: 'arrows' })).toBeLessThan(300)
    await page.reload()
    await page.getByTestId('who-card').filter({ hasText: 'Priya' }).click()
    const dialog = page.getByTestId('arrow-ask-dialog')
    await expect(dialog.getByTestId('arrow-dots').locator('i')).toHaveCount(6)
    await dialog.getByTestId('arrow-ask-left').click()
    await expect(dialog.getByTestId('arrow-dots')).toHaveAttribute('data-entered', '1')
    await page.keyboard.press('Escape')
    await expect(dialog).toHaveCount(0)
    await expect(page.getByTestId('who-is-watching')).toBeVisible()
    // Opened again it starts empty.
    await page.getByTestId('who-card').filter({ hasText: 'Priya' }).click()
    await expect(dialog.getByTestId('arrow-dots')).toHaveAttribute('data-entered', '0')
    for (const arrow of ['left', 'right', 'down', 'up', 'left', 'right'] as const) await dialog.getByTestId(`arrow-ask-${arrow}`).click()
    await expect(page.getByTestId('home')).toBeVisible()
  })

  test('a password: typed in a focused field, Enter switches', async ({ page }) => {
    expect(await asOwner(page, 'PUT', '/users/u4/pin', { pin: 'open sesame 9', kind: 'password' })).toBeLessThan(300)
    await page.reload()
    const card = page.getByTestId('who-card').filter({ hasText: 'Priya' })
    await expect(card).toHaveAttribute('aria-label', /password needed/)
    await card.click()
    const dialog = page.getByTestId('password-dialog')
    await expect(dialog.getByRole('heading', { name: 'Password for Priya' })).toBeVisible()
    const field = dialog.getByLabel('Password', { exact: true })
    // Focus starts in the field, which is what brings up a TV's keyboard.
    await expect(field).toBeFocused()
    await expect(field).toHaveAttribute('type', 'password')
    await page.keyboard.type('open sesame')
    await page.keyboard.press('Enter')
    await expect(dialog.getByTestId('password-error')).toHaveText('That password is not right. Try again.')
    await expect(field).toHaveValue('')
    await expect(field).toBeFocused()
    await page.keyboard.type('open sesame 9')
    await page.keyboard.press('Enter')
    await expect(page.getByTestId('home')).toBeVisible()
    await expect(page.getByTestId('nav-profile')).toHaveAttribute('aria-label', 'Profile Priya')
  })

  test('a number PIN still gets the keypad, and its focused key is highlighted', async ({ page }) => {
    await page.getByTestId('who-card').filter({ hasText: 'Kids' }).click()
    const dialog = page.getByTestId('pin-dialog')
    const five = dialog.getByRole('button', { name: '5', exact: true })
    await expect(five).toHaveAttribute('data-focused', 'true')
    const ring = (name: string) => dialog.getByRole('button', { name, exact: true }).evaluate((el) => { const s = getComputedStyle(el); return `${s.outlineStyle} ${s.outlineWidth} ${s.boxShadow}` })
    await expect.poll(() => ring('5')).not.toBe(await ring('2'))
    await page.keyboard.press('ArrowUp')
    await expect(dialog.getByRole('button', { name: '2', exact: true })).toHaveAttribute('data-focused', 'true')
    await expect.poll(() => ring('2')).not.toBe(await ring('4'))
  })
})

test('the person page picks one way to sign in: a password, typed twice', async ({ page }) => {
  await page.addInitScript(() => {
    const sent: unknown[] = []
    ;(window as unknown as { sentPins: unknown[] }).sentPins = sent
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const request = new Request(input, init)
      if (request.method === 'PUT' && new URL(request.url).pathname.endsWith('/users/u5/pin')) sent.push(await request.clone().json())
      return original(request)
    }
  })
  await enter(page, '/users/u5', 'owner')
  const row = page.getByTestId('user-pin')
  await expect(row.getByTestId('user-pin-kind')).toHaveText('No PIN')
  await page.getByTestId('user-pin-set').click()
  const choice = page.getByTestId('secret-choice')
  for (const name of ['Number PIN', 'Password', 'Arrow pattern']) await expect(choice.getByRole('button', { name: new RegExp(`^${name}`) })).toBeVisible()
  await page.getByTestId('secret-kind-password').click()
  const dialog = page.getByTestId('password-dialog')
  await expect(dialog.getByRole('heading', { name: 'New password for Mara Ellison' })).toBeVisible()
  await expect(dialog.getByLabel('Password', { exact: true })).toBeFocused()
  // Too short, then two that differ, then the same twice.
  await page.keyboard.type('abc')
  await dialog.getByTestId('password-ok').click()
  await expect(dialog.getByTestId('password-error')).toHaveText('Use 6 to 64 characters.')
  await dialog.getByLabel('Password', { exact: true }).fill('river stones')
  await dialog.getByLabel('Password again', { exact: true }).fill('river stone')
  await dialog.getByTestId('password-ok').click()
  await expect(dialog.getByTestId('password-error')).toHaveText('Those two passwords are not the same.')
  await dialog.getByLabel('Password again', { exact: true }).fill('river stones')
  await page.keyboard.press('Enter')
  await expect(dialog).toHaveCount(0)
  await expect(row.getByTestId('user-pin-kind')).toHaveText('Signs in with a password')
  await expect(page.getByTestId('user-pin-clear')).toHaveText('Remove password')
  expect(await page.evaluate(() => (window as unknown as { sentPins: unknown[] }).sentPins)).toEqual([{ pin: 'river stones', kind: 'password' }])
})
