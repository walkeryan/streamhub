import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

type Options = { partial?: boolean; failStop?: boolean; failRefresh?: boolean; delayStop?: boolean }

async function setup(page: Page, options: Options = {}, admin = true) {
  await page.addInitScript((options) => {
    const original = window.fetch.bind(window)
    const state = { gets: 0, stops: 0, resumes: 0, paused: false, active: 2, failRefresh: false, confirmations: [] as unknown[] }
    ;(window as unknown as { recoveryFixture: typeof state }).recoveryFixture = state
    const status = () => ({ paused: state.paused, active_sessions: state.active, shared_upstreams: 1, hls_sessions: 1, http_sessions: 0, external_sessions_tracked: false })
    window.fetch = async (input, init) => {
      const req = new Request(input, init)
      const path = new URL(req.url).pathname
      if (path === '/api/v1/playback/recovery') {
        state.gets++
        if (state.failRefresh) return Response.json({ message: 'private error' }, { status: 503 })
        return Response.json(status())
      }
      if (path === '/api/v1/playback/recovery/stop') {
        state.stops++
        state.confirmations.push(await req.json())
        if (options.delayStop) await new Promise<void>((resolve) => {
          ;(window as unknown as { releaseStop: () => void }).releaseStop = resolve
        })
        if (options.failStop) throw new TypeError('Fixture transport failure')
        state.paused = true
        state.active = options.partial ? 1 : 0
        return Response.json({ ...status(), stopped: options.partial ? 3 : 4, failed: options.partial ? 1 : 0, cleanup_failed: options.partial ? 1 : 0 })
      }
      if (path === '/api/v1/playback/recovery/resume') {
        state.resumes++
        state.confirmations.push(await req.json())
        state.paused = false
        return Response.json(status())
      }
      return original(req)
    }
  }, options)
  await signIn(page)
  if (admin) await page.evaluate(async () => {
    const path = '/src/store/session.ts'
    const { useSession } = await import(/* @vite-ignore */ path)
    const s = useSession.getState()
    s.setSession(s.access, s.refresh, { ...s.profile, is_admin: true })
  })
  await page.getByRole('link', { name: 'Settings', exact: true }).click()
  await page.getByTestId('tab-playback').click()
  if (admin) {
    await expect(page.getByTestId('stream-recovery')).toContainText('2 Streamhub-owned media sessions/connections')
    await page.evaluate((failRefresh) => {
      const state = (window as unknown as { recoveryFixture: { active: number; failRefresh: boolean } }).recoveryFixture
      state.active = 4
      state.failRefresh = failRefresh
    }, !!options.failRefresh)
  }
}
const counts = (page: Page) => page.evaluate(() => (window as unknown as { recoveryFixture: { gets: number; stops: number; resumes: number; confirmations: unknown[] } }).recoveryFixture)

test('stop confirmation refreshes counts, defaults to Cancel and Escape restores opener focus', async ({ page }) => {
  await setup(page)
  const opener = page.getByRole('button', { name: 'Stop all streams', exact: true })
  await opener.click()
  const dialog = page.getByRole('dialog', { name: 'Stop all streams?' })
  await expect(dialog).toContainText('4 Streamhub-owned media sessions/connections')
  await expect(dialog).toContainText('interrupts other Streamhub viewers')
  await expect(dialog).toContainText('external IPTV apps')
  await expect(dialog.getByRole('button', { name: 'Cancel', exact: true })).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(opener).toHaveAttribute('data-focused', 'true')
  expect((await counts(page)).stops).toBe(0)
  await page.keyboard.press('Enter')
  await expect(dialog.getByRole('button', { name: 'Cancel', exact: true })).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  await expect(dialog).toHaveCount(0)
  expect((await counts(page)).stops).toBe(0)
})

test('keyboard confirm stops once, reports paused, and Resume requires separate confirmation', async ({ page }) => {
  await setup(page)
  await page.getByRole('button', { name: 'Stop all streams', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Stop all streams?' })
  await expect(dialog.getByRole('button', { name: 'Cancel', exact: true })).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowRight')
  await expect(dialog.getByRole('button', { name: 'Confirm stop all streams', exact: true })).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  await expect(dialog).toHaveCount(0)
  await expect(page.getByTestId('stream-recovery')).toContainText('Stopped 4 Streamhub-owned')
  expect((await counts(page)).stops).toBe(1)
  await page.getByRole('button', { name: 'Resume new playback', exact: true }).click()
  const resume = page.getByRole('dialog', { name: 'Resume new playback?' })
  await expect(resume).toContainText('Previously stopped stream URLs remain invalid')
  expect((await counts(page)).resumes).toBe(0)
  await expect(resume.getByRole('button', { name: 'Cancel', exact: true })).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('Enter')
  await expect(resume).toHaveCount(0)
  await expect(page.getByTestId('stream-recovery')).toContainText('New playback is allowed')
  expect((await counts(page)).confirmations).toEqual([{ confirm: true }, { confirm: true }])
})

test('partial stop and cleanup failures stay honest and permit Stop retry', async ({ page }) => {
  await setup(page, { partial: true })
  await page.getByRole('button', { name: 'Stop all streams', exact: true }).click()
  await page.getByRole('button', { name: 'Confirm stop all streams', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('1 stop failures and 1 cleanup failures remain')
  await expect(page.getByRole('button', { name: 'Resume new playback', exact: true })).toHaveAttribute('aria-disabled', 'true')
  await page.getByRole('button', { name: 'Stop all streams', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('1 Streamhub-owned media sessions/connections')
  await page.getByRole('button', { name: 'Confirm stop all streams', exact: true }).click()
  expect((await counts(page)).stops).toBe(2)
})

test('uncertain stop outcome reports no success and does not automatically retry', async ({ page }) => {
  await setup(page, { failStop: true })
  await page.getByRole('button', { name: 'Stop all streams', exact: true }).click()
  await page.getByRole('button', { name: 'Confirm stop all streams', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Could not confirm the operation completed')
  await expect(page.getByRole('alert')).not.toContainText('private error')
  expect((await counts(page)).stops).toBe(1)
})

test('failed fresh status disables confirmation', async ({ page }) => {
  await setup(page, { failRefresh: true })
  await page.getByRole('button', { name: 'Stop all streams', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('Current session count could not be verified')
  await expect(dialog.getByRole('button', { name: 'Confirm stop all streams', exact: true })).toHaveAttribute('aria-disabled', 'true')
  await page.keyboard.press('Escape')
  expect((await counts(page)).stops).toBe(0)
})

test('non-admin profile sees no recovery controls and makes no status request', async ({ page }) => {
  await setup(page, {}, false)
  await expect(page.getByTestId('stream-recovery')).toHaveCount(0)
  expect((await counts(page)).gets).toBe(0)
})

test('repeated Select during a pending stop sends one mutation and Back cannot hide its outcome', async ({ page }) => {
  await setup(page, { delayStop: true })
  await page.getByRole('button', { name: 'Stop all streams', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Stop all streams?' })
  await expect(dialog.getByRole('button', { name: 'Cancel', exact: true })).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('Enter')
  await expect.poll(async () => (await counts(page)).stops).toBe(1)
  await page.keyboard.press('Enter')
  await page.keyboard.press('Escape')
  await expect(dialog).toBeVisible()
  expect((await counts(page)).stops).toBe(1)
  await page.evaluate(() => (window as unknown as { releaseStop: () => void }).releaseStop())
  await expect(dialog).toHaveCount(0)
  await expect(page.getByTestId('stream-recovery')).toContainText('Stopped 4')
})

test('a hung stop has a bounded wait, reports uncertain outcome and ignores late success', async ({ page }) => {
  await setup(page, { delayStop: true })
  await page.clock.install()
  await page.getByRole('button', { name: 'Stop all streams', exact: true }).click()
  await page.getByRole('button', { name: 'Confirm stop all streams', exact: true }).click()
  await expect.poll(async () => (await counts(page)).stops).toBe(1)
  await page.clock.fastForward(15_001)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByRole('alert')).toContainText('Could not confirm the operation completed')
  await expect(page.getByRole('button', { name: 'Stop all streams', exact: true })).not.toHaveAttribute('aria-disabled', 'true')
  await page.evaluate(() => (window as unknown as { releaseStop: () => void }).releaseStop())
  await expect(page.getByRole('alert')).toContainText('Could not confirm the operation completed')
  expect((await counts(page)).stops).toBe(1)
})
