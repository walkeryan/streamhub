import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

for (const status of [400, 401, 403, 404, 410, 422]) {
  test(`resolution ${status} stops automatic reconnect but permits an explicit retry`, async ({ page }) => {
    await page.addInitScript((status) => {
      const original = window.fetch.bind(window)
      const state = window as unknown as { failedResolves: number }
      state.failedResolves = 0
      window.fetch = async (input, init) => {
        const request = new Request(input, init)
        if (new URL(request.url).pathname === '/api/v1/auth/refresh') {
          // The generic mock refresh always returns p1, which would switch the
          // Guest fixture. Preserve this session to exercise exhausted 401 retry.
          const sessionPath = '/src/store/session.ts'
          const { useSession } = await import(/* @vite-ignore */ sessionPath)
          const current = useSession.getState()
          return Response.json({ access_token: current.access, refresh_token: current.refresh, profile: current.profile })
        }
        if (new URL(request.url).pathname === '/api/v1/playback/resolve') {
          state.failedResolves++
          return Response.json({ code: 'not_found', message: 'Private provider diagnostic must not appear' }, { status })
        }
        return original(request)
      }
    }, status)
    await signIn(page)
    await page.clock.install()
    await page.getByRole('link', { name: 'Live TV', exact: true }).click()
    await page.getByTestId('channel-row').first().click()
    await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'error')
    await expect(page.getByText('Connection lost. Reconnecting…', { exact: true })).toHaveCount(0)
    await expect(page.getByText('Private provider diagnostic must not appear', { exact: true })).toHaveCount(0)
    const count = () => page.evaluate(() => (window as unknown as { failedResolves: number }).failedResolves)
    const initial = await count()
    await page.clock.fastForward(60000)
    expect(await count()).toBe(initial)
    await page.getByRole('button', { name: 'Try again', exact: true }).click()
    await expect.poll(count).toBe(initial + (status === 401 ? 2 : 1))
    await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'error')
  })
}
