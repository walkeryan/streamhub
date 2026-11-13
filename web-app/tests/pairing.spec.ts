import { expect, test, type Page } from '@playwright/test'

// Connecting a TV signs it in as whoever approves its code. A link that carries a code is therefore
// how someone else's device gets signed in as you, so a code is never approved in one click from a
// link: it is checked first, and a second screen says what is asking before anything is approved.
const OWNER = { id: 'p1', name: 'Ryan', avatar: null, is_admin: true, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' }

async function enter(page: Page, path: string, { pairOld }: { signedIn?: boolean; pairOld?: boolean } = {}) {
  await page.addInitScript(({ session, pairOld }) => {
    sessionStorage.setItem('mock.accepted', JSON.stringify(Array.from({ length: 60 }, (_, i) => `p${i + 1}`)))
    if (pairOld) sessionStorage.setItem('mock.pair.old', '1')
    localStorage.setItem('sh.session.v1', JSON.stringify({ access: `mock-access-${session.id}`, refresh: `mock-refresh-${session.id}`, profile: session }))
  }, { session: OWNER, pairOld })
  await page.goto(path)
}
const button = (page: Page, name: string) => page.getByRole('button', { name, exact: true })

test.describe('connecting a TV', () => {
  test('a code that arrived in a link is never approved in one click', async ({ page }) => {
    await enter(page, '/pair?code=abc234', { signedIn: true })
    await expect(page.getByTestId('pair-from-link')).toContainText('ABC234')
    // Nothing on this screen approves.
    await expect(page.getByTestId('pair-approve')).toHaveCount(0)
    await page.getByLabel('TV code', { exact: true }).press('Enter')
    const review = page.getByTestId('pair-review-panel')
    await expect(review.getByRole('heading', { name: 'A TV is asking to sign in as Ryan' })).toBeVisible()
    await expect(review).toContainText('Only approve if you are holding its remote and it shows this code')
    await expect(page.getByTestId('pair-code-shown')).toHaveText('ABC234')
    await expect(page.getByTestId('pair-asking')).toContainText('Asked for from 203.0.113.24')
    await expect(page.getByTestId('pair-asking')).toContainText('StreamHub TV/1.0')
    // The cursor waits on the way out, not on Approve.
    await expect(button(page, 'Not my TV')).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('Enter')
    await expect(page.getByLabel('TV code', { exact: true })).toBeVisible()
    await expect(page.getByTestId('pair-done')).toHaveCount(0)

    await page.getByLabel('TV code', { exact: true }).press('Enter')
    await expect(button(page, 'Not my TV')).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('ArrowRight')
    await expect(button(page, 'Approve this TV')).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('Enter')
    await expect(page.getByTestId('pair-done')).toContainText('signing in as Ryan')
  })

  test('a code the server does not know stops at the first screen', async ({ page }) => {
    await enter(page, '/pair', { signedIn: true })
    await page.getByLabel('TV code', { exact: true }).fill('BADBAD')
    await page.getByTestId('pair-review').click()
    await expect(page.getByRole('alert')).toContainText('not valid or has expired')
    await expect(page.getByTestId('pair-review-panel')).toHaveCount(0)
  })

  test('a server that cannot say what is asking still makes the person confirm', async ({ page }) => {
    await enter(page, '/pair', { signedIn: true, pairOld: true })
    await page.getByLabel('TV code', { exact: true }).fill('ABC234')
    await page.keyboard.press('Enter')
    await expect(page.getByTestId('pair-asking')).toContainText('cannot say where the request came from')
    await page.getByTestId('pair-approve').click()
    await expect(page.getByTestId('pair-done')).toBeVisible()
  })
})
