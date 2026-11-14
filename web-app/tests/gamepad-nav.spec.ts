import { expect, test, type Page } from '@playwright/test'
import { focusedLabel, signIn } from './helpers'

// A stand-in controller: the page's Gamepad API answers with one standard-mapping pad whose buttons
// the test presses, the way an Xbox controller shows up in Edge.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const pressed = Array(17).fill(false)
    const pad = {
      id: 'Test controller', index: 0, connected: true, mapping: 'standard', timestamp: 0,
      axes: [0, 0, 0, 0],
      get buttons() { return pressed.map((p) => ({ pressed: p, touched: p, value: p ? 1 : 0 })) },
    }
    Object.defineProperty(navigator, 'getGamepads', { value: () => [pad, null, null, null] })
    ;(window as unknown as { __pad: boolean[] }).__pad = pressed
  })
})

/** One tap of a standard-mapping button: held for a few frames, then let go. */
async function tap(page: Page, button: number) {
  await page.evaluate((b) => { (window as unknown as { __pad: boolean[] }).__pad[b] = true }, button)
  await page.waitForTimeout(80)
  await page.evaluate((b) => { (window as unknown as { __pad: boolean[] }).__pad[b] = false }, button)
  await page.waitForTimeout(80)
}

const UP = 12, DOWN = 13, RIGHT = 15, A = 0, B = 1

test('controller only: D-pad moves focus, A opens, B goes back and restores focus', async ({ page }) => {
  await signIn(page)
  await expect(page.locator('.fx[data-focused="true"]').first()).toContainText('Play')

  await tap(page, DOWN)
  await tap(page, RIGHT)
  const tileLabel = await focusedLabel(page)
  expect(tileLabel).toBeTruthy()
  await tap(page, A)
  await expect(page).toHaveURL(/\/title\//)
  await expect(page.getByTestId('title-detail')).toBeVisible()

  await tap(page, B)
  await expect(page).toHaveURL(/\/$/)
  await expect.poll(() => focusedLabel(page)).toBe(tileLabel)
  await tap(page, UP)
  await expect(page.locator('.fx[data-focused="true"]').first()).toContainText('Play')
})

test('a held direction repeats like a held remote key', async ({ page }) => {
  await signIn(page)
  const seen = new Set([await focusedLabel(page)])
  await page.evaluate(() => { (window as unknown as { __pad: boolean[] }).__pad[13] = true })
  // One step at once, then steady repeats after the pause: several rows down within a second.
  for (let i = 0; i < 10; i++) { await page.waitForTimeout(100); seen.add(await focusedLabel(page)) }
  await page.evaluate(() => { (window as unknown as { __pad: boolean[] }).__pad[13] = false })
  expect(seen.size).toBeGreaterThanOrEqual(4)
})
