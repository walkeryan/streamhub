import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('quick stop and resume cannot refresh an old playback intent; explicit Retry can', async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    const state = window as unknown as { recoveryFixture: { generation: string; reads: number; resolves: string[] } }
    state.recoveryFixture = { generation: 'before-stop', reads: 0, resolves: [] }
    window.fetch = async (input, init) => {
      const request = new Request(input, init)
      const path = new URL(request.url).pathname
      if (path === '/api/v1/playback/generation') {
        state.recoveryFixture.reads++
        return Response.json({ generation: state.recoveryFixture.generation })
      }
      if (path === '/api/v1/playback/resolve') {
        const body = await request.json()
        state.recoveryFixture.resolves.push(body.recovery_generation)
        return body.recovery_generation === state.recoveryFixture.generation
          ? Response.json({ code: 'upstream_unavailable', message: 'Fixture unavailable' }, { status: 503 })
          : Response.json({ code: 'playback_stopped', message: 'Stopped' }, { status: 403 })
      }
      return original(request)
    }
  })
  await signIn(page)
  await page.clock.install()
  await page.getByRole('link', { name: 'Live TV', exact: true }).click()
  await page.getByTestId('channel-row').first().click()
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'reconnecting')
  const snapshot = () => page.evaluate(() => (window as unknown as { recoveryFixture: { reads: number; resolves: string[] } }).recoveryFixture)
  // Development StrictMode may cancel and repeat the initial mount fetch.
  // The invariant is no additional generation read during automatic recovery.
  const initialReads = (await snapshot()).reads
  expect(initialReads).toBeGreaterThan(0)
  // Admin stops AND resumes before the player's backoff expires.
  await page.evaluate(() => { (window as unknown as { recoveryFixture: { generation: string } }).recoveryFixture.generation = 'after-stop' })
  await page.clock.fastForward(510)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'error')
  await expect(page.getByText(/Playback was stopped by an administrator/)).toBeVisible()
  expect(await snapshot()).toMatchObject({ reads: initialReads, resolves: ['before-stop', 'before-stop'] })
  await page.getByTestId('video').evaluate((v) => {
    v.dispatchEvent(new Event('error'))
    v.dispatchEvent(new Event('playing'))
  })
  await page.clock.fastForward(60000)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'error')
  expect(await snapshot()).toMatchObject({ reads: initialReads, resolves: ['before-stop', 'before-stop'] })
  await page.getByRole('button', { name: 'Try again', exact: true }).click()
  await expect.poll(async () => (await snapshot()).resolves).toEqual(['before-stop', 'before-stop', 'after-stop'])
  expect((await snapshot()).reads).toBe(initialReads + 1)
})
