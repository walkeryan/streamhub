import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('provider archive is honest, D-pad adjustable and returns to Live', async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const req = new Request(input, init), url = new URL(req.url)
      if (url.pathname === '/api/v1/playback/progress') return new Response(null, { status: 204 })
      if (url.pathname === '/api/v1/playback/rewind') return Response.json({ mode: 'provider_archive', advertised_start: '2026-11-01T05:30:00Z', advertised_end: '2026-11-01T06:30:00Z', verified: false, recording_enabled: false, granularity_seconds: 60, reason: 'Fixture archive' })
      if (url.pathname === '/api/v1/playback/resolve') {
        const body = await req.json()
        sessionStorage.setItem('rewind.request', JSON.stringify(body))
        // A finite local fixture avoids any network media/provider session.
        return Response.json({ id: body.id, mode: 'direct', live: !body.rewind_at && !body.catchup_programme_id, url: '/mock/sample.webm', mime: 'video/webm', title: 'Archive fixture', duration_ms: 3600000 })
      }
      return original(req)
    }
  })
  await signIn(page)
  await page.goto('/play/ch%3Aiptv%3A1?catchup=prog%3A1')
  await expect(page.getByTestId('rewind-controls')).toContainText('availability unverified')
  await expect(page.getByRole('button', { name: 'Go Live', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '−10 s', exact: true })).toBeVisible()
  const position = page.getByRole('button', { name: 'Try 1 minutes ago. Left and Right adjust, Select plays.', exact: true })
  await position.click()
  await expect.poll(() => page.evaluate(() => JSON.parse(sessionStorage.getItem('rewind.request') ?? '{}').rewind_at)).toBe('2026-11-01T06:29:00.000Z')
  await expect(position).toBeVisible()
  await page.keyboard.press('ArrowLeft')
  await expect(page.getByText('Try 2 min ago', { exact: true })).toBeVisible()
  await page.keyboard.press('Enter')
  await expect.poll(() => page.evaluate(() => JSON.parse(sessionStorage.getItem('rewind.request') ?? '{}').rewind_at)).toBe('2026-11-01T06:28:00.000Z')
  await page.getByRole('button', { name: 'Go Live', exact: true }).click()
  await expect.poll(() => page.evaluate(() => JSON.parse(sessionStorage.getItem('rewind.request') ?? '{}').rewind_at)).toBeNull()
})
