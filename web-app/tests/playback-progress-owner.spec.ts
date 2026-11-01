import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('late player events cannot report the old title with a newly selected profile token', async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const request = new Request(input, init)
      if (new URL(request.url).pathname === '/api/v1/playback/progress') {
        const reports = JSON.parse(sessionStorage.getItem('owner.reports') ?? '[]')
        reports.push({ body: await request.json(), authorization: request.headers.get('authorization') })
        sessionStorage.setItem('owner.reports', JSON.stringify(reports))
        return new Response(null, { status: 204 })
      }
      return original(request)
    }
  })
  await signIn(page)
  await page.getByRole('link', { name: 'Live TV', exact: true }).click()
  await page.getByTestId('channel-row').first().click()
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  await page.getByTestId('video').evaluate((video: HTMLVideoElement) => video.pause())
  const reports = () => page.evaluate(() => JSON.parse(sessionStorage.getItem('owner.reports') ?? '[]') as Array<{ body: { expected_profile_id: string }; authorization: string }>)
  await expect.poll(async () => (await reports()).length).toBeGreaterThan(0)
  expect((await reports()).every(report => report.body.expected_profile_id === 'p2')).toBe(true)
  await page.evaluate(async () => {
    const sessionPath = '/src/store/session.ts'
    const { useSession } = await import(/* @vite-ignore */ sessionPath)
    const oldProfile = useSession.getState().profile
    const video = document.querySelector('video')!
    useSession.getState().setSession('new-profile-access', 'new-profile-refresh', { ...oldProfile, id: 'switched-profile', name: 'Switched' })
    // Deliberately fire before React can clean up the previous effect listeners.
    video.dispatchEvent(new Event('pause'))
    video.dispatchEvent(new Event('ended'))
    window.dispatchEvent(new Event('pagehide'))
  })
  await expect.poll(async () => (await reports()).filter(report => report.authorization === 'Bearer new-profile-access' && report.body.expected_profile_id === 'p2').length).toBe(0)
  expect((await reports()).filter(report => report.body.expected_profile_id === 'p2').every(report => report.authorization !== 'Bearer new-profile-access')).toBe(true)
})
