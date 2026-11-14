import { expect, test, type Page } from '@playwright/test'

// Private PIN entry: on a shared TV a parent enters a PIN with the remote while others watch the
// screen. The keypad never shows which key has focus, focus returns to 5 after every key, and arrows
// are clamped to the keypad, so each digit is a short walk from 5 that can be done without looking.

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

const dialog = (page: Page) => page.getByTestId('pin-dialog')
const key = (page: Page, name: string) => dialog(page).getByRole('button', { name, exact: true })
const dots = (page: Page) => dialog(page).getByTestId('pin-dots').locator('i')
const focusedKey = (page: Page) => dialog(page).locator('[data-focus-key][data-focused="true"]').getAttribute('data-focus-key')
const atHome = (page: Page) => expect(key(page, '5')).toHaveAttribute('data-focused', 'true')

/** The walk from 5 to each key, as a parent would learn it. */
const WALK: Record<string, string[]> = {
  '1': ['ArrowUp', 'ArrowLeft'], '2': ['ArrowUp'], '3': ['ArrowUp', 'ArrowRight'],
  '4': ['ArrowLeft'], '5': [], '6': ['ArrowRight'],
  '7': ['ArrowDown', 'ArrowLeft'], '8': ['ArrowDown'], '9': ['ArrowDown', 'ArrowRight'],
  '0': ['ArrowDown', 'ArrowDown'], erase: ['ArrowDown', 'ArrowDown', 'ArrowLeft'], ok: ['ArrowDown', 'ArrowDown', 'ArrowRight'],
}
async function press(page: Page, target: string) {
  for (const arrow of WALK[target]!) await page.keyboard.press(arrow)
  await page.keyboard.press('Enter')
}
/** A PIN entered with the remote alone: arrows and OK, checking focus is back on 5 after each digit. */
async function remotePin(page: Page, pin: string, already = 0) {
  for (const [i, digit] of [...pin].entries()) {
    await press(page, digit)
    await expect(dots(page)).toHaveCount(already + i + 1)
    await atHome(page)
  }
}

/** The styles that could give a focused key away. */
const look = (page: Page, name: string) => key(page, name).evaluate((el) => {
  const s = getComputedStyle(el)
  return [s.outlineStyle, s.outlineWidth, s.outlineColor, s.boxShadow, s.backgroundColor, s.color, s.transform, s.opacity, s.borderColor, s.filter].join(' | ')
})

test.describe('private PIN entry', () => {
  test.beforeEach(async ({ page }) => {
    await enter(page, '/', 'guest', true)
    await expect(page.getByTestId('who-is-watching')).toBeVisible()
  })
  const open = async (page: Page, name: string) => {
    await page.getByTestId('who-card').filter({ hasText: name }).click()
    await expect(dialog(page)).toBeVisible()
    await atHome(page)
  }

  test('focus starts on 5 and no key looks any different when it has focus', async ({ page }) => {
    await open(page, 'Kids')
    await expect(dialog(page).getByTestId('pin-private')).toHaveText('Your place on the keypad is hidden. Each digit starts from 5.')
    // Every digit key computes the same look whether or not it holds focus.
    const focused = await look(page, '5')
    for (const digit of '1234678 90'.replace(' ', '')) expect(await look(page, digit)).toBe(focused)
    // And the keypad renders pixel for pixel the same wherever focus is.
    const pad = dialog(page).locator('.pin-pad')
    const atFive = await pad.screenshot({ animations: 'disabled' })
    for (const arrow of ['ArrowUp', 'ArrowLeft']) await page.keyboard.press(arrow)
    expect(await focusedKey(page)).toBe('pin:1')
    expect(await look(page, '1')).toBe(focused)
    expect((await pad.screenshot({ animations: 'disabled' })).equals(atFive)).toBe(true)
    await page.keyboard.press('ArrowDown'); await page.keyboard.press('ArrowDown'); await page.keyboard.press('ArrowRight')
    expect(await focusedKey(page)).toBe('pin:8')
    expect((await pad.screenshot({ animations: 'disabled' })).equals(atFive)).toBe(true)
    // Nothing else in the dialog names the focused key.
    await expect(dialog(page)).not.toContainText(/selected|focused/i)
  })

  test('arrows clamp at the edges without wrapping or leaving the keypad', async ({ page }) => {
    await open(page, 'Kids')
    const walk = async (arrow: string, times: number, expected: string) => {
      for (let i = 0; i < times; i++) {
        await page.keyboard.press(arrow)
        expect(await focusedKey(page)).toMatch(/^pin:([0-9]|erase|ok)$/)
      }
      expect(await focusedKey(page)).toBe(expected)
    }
    await walk('ArrowUp', 4, 'pin:2')
    await walk('ArrowLeft', 4, 'pin:1')
    await walk('ArrowRight', 5, 'pin:3')
    await walk('ArrowDown', 6, 'pin:ok')
    await walk('ArrowLeft', 1, 'pin:0')
    await walk('ArrowLeft', 3, 'pin:erase')
    await walk('ArrowDown', 3, 'pin:erase')
    // The disabled-looking erase and OK keys still hold their place, so counted walks stay true.
    await expect(key(page, 'Erase last digit')).toHaveAttribute('aria-disabled', 'true')
    await expect(page.getByTestId('pin-ok')).toHaveAttribute('aria-disabled', 'true')
    // Back still closes the dialog.
    await page.keyboard.press('Escape')
    await expect(dialog(page)).toHaveCount(0)
    await expect(page.getByTestId('who-is-watching')).toBeVisible()
  })

  test('a six-digit PIN entered with arrows and OK alone switches the user', async ({ page }) => {
    expect(await asOwner(page, 'PUT', '/users/u4/pin', { pin: '159073' })).toBeLessThan(300)
    await open(page, 'Priya')
    // OK on a digit key enters that digit, even past four digits.
    await remotePin(page, '159073')
    await expect(dialog(page).getByTestId('pin-dots')).toHaveAttribute('aria-label', '6 digits entered')
    // Erase from the keypad goes back to 5 as well.
    await press(page, 'erase')
    await expect(dots(page)).toHaveCount(5)
    await atHome(page)
    await remotePin(page, '3', 5)
    await press(page, 'ok')
    await expect(page.getByTestId('home')).toBeVisible()
    await expect(page.getByTestId('nav-profile')).toHaveAttribute('aria-label', 'Profile Priya')
  })

  test('after a wrong PIN focus is back on 5', async ({ page }) => {
    await open(page, 'Kids')
    await remotePin(page, '9999')
    await press(page, 'ok')
    await expect(dialog(page).getByTestId('pin-error')).toHaveText('That PIN is not right. Try again.')
    await expect(dots(page)).toHaveCount(0)
    await atHome(page)
    await remotePin(page, '1234')
    await press(page, 'ok')
    await expect(page.getByTestId('home')).toBeVisible()
  })

  test('number keys type digits, Backspace erases, Enter submits; an arrow gives Enter back to the keypad', async ({ page }) => {
    await open(page, 'Kids')
    await page.keyboard.type('12399')
    await expect(dots(page)).toHaveCount(5)
    await page.keyboard.press('Backspace')
    await page.keyboard.press('Backspace')
    await expect(dots(page)).toHaveCount(3)
    // After typing, Enter submits, so a short PIN does nothing yet.
    await page.keyboard.press('Enter')
    await expect(dots(page)).toHaveCount(3)
    // An arrow means the remote again: OK presses the focused key (4, one left of 5).
    await page.keyboard.press('ArrowLeft')
    await page.keyboard.press('Enter')
    await expect(dots(page)).toHaveCount(4)
    await atHome(page)
    await press(page, 'ok')
    await expect(page.getByTestId('home')).toBeVisible()
  })
})

test('a new PIN entered twice by remote: focus is back on 5 for the confirm step', async ({ page }) => {
  // The mock answers inside the page, so read what the app sends from its own fetch.
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
  await page.getByTestId('user-pin-set').click()
  await expect(dialog(page)).toBeVisible()
  await atHome(page)
  await remotePin(page, '864200')
  await press(page, 'ok')
  await expect(dialog(page)).toContainText('Enter it once more to confirm.')
  await expect(dots(page)).toHaveCount(0)
  await atHome(page)
  await remotePin(page, '864200')
  await press(page, 'ok')
  await expect(dialog(page)).toHaveCount(0)
  expect(await page.evaluate(() => (window as unknown as { sentPins: unknown[] }).sentPins)).toEqual([{ pin: '864200' }])
})
