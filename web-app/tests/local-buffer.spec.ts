import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    const value = { mode: 'local_buffer', start: '2026-11-01T10:00:00Z', end: '2026-11-01T10:01:00Z', buffered_seconds: 60, target_seconds: 3600, recording_enabled: true }
    window.fetch = async (input, init) => {
      const request = new Request(input, init), url = new URL(request.url)
      if (url.pathname === '/api/v1/playback/progress') return new Response(null, { status: 204 })
      if (url.pathname === '/api/v1/playback/resolve') return Response.json({ id: 'ch:iptv:1', title: 'Fixture buffer', live: true, mode: 'hls', url: `${location.origin}/api/v1/stream/fixture-token/index.m3u8`, buffer_available: true, resume_position_ms: 0 })
      if (url.pathname.startsWith('/api/v1/playback/buffer/')) {
        const body = await request.json()
        const records = JSON.parse(sessionStorage.getItem('buffer.calls') ?? '[]')
        records.push({ path: url.pathname, body }); sessionStorage.setItem('buffer.calls', JSON.stringify(records))
        if (url.pathname.endsWith('/lease')) return Response.json({ lease_id: 'fixture-lease', session_id: 'fixture-session', window: value })
        if (url.pathname.endsWith('/heartbeat')) return Response.json(value)
        if (url.pathname.endsWith('/seek')) return Response.json({ url: `${location.origin}/api/v1/stream/fixture-token/index.m3u8?lease_id=fixture-lease`, window: value })
        return new Response(null, { status: 204 })
      }
      return original(request)
    }
  })
  await page.context().route('**/api/v1/stream/fixture-token/**', async (route) => { await new Promise((resolve) => setTimeout(resolve, 500)); await route.fulfill({ contentType: 'application/vnd.apple.mpegurl', body: '#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n' }) })
  await signIn(page)
})

test('local window scrubs with arrows and Select, Go Live retains lease, exit releases', async ({ page }) => {
  await page.evaluate(() => { history.pushState({}, '', '/play/ch%3Aiptv%3A1'); window.dispatchEvent(new PopStateEvent('popstate')) })
  const controls = page.getByTestId('local-buffer-controls')
  await expect(controls).toContainText('1m 0s buffered')
  const slider = page.getByRole('button', { name: 'Buffered playback position. Left and Right adjust, Select plays.' })
  await page.keyboard.press("ArrowDown")
  await slider.hover()
  await page.keyboard.press('ArrowLeft')
  await page.keyboard.press('Enter')
  await expect.poll(() => page.evaluate(() => JSON.parse(sessionStorage.getItem('buffer.calls') ?? '[]').filter((x: {path: string}) => x.path.endsWith('/seek')).at(-1)?.body.at)).toBe('2026-11-01T10:00:49.000Z')
  await page.getByRole('button', { name: 'Go Live', exact: true }).click()
  await expect.poll(() => page.evaluate(() => JSON.parse(sessionStorage.getItem('buffer.calls') ?? '[]').filter((x: {path: string}) => x.path.endsWith('/seek')).at(-1)?.body.at)).toBeNull()
  await page.getByRole('button', { name: 'Back', exact: true }).click()
  await expect.poll(() => page.evaluate(() => JSON.parse(sessionStorage.getItem('buffer.calls') ?? '[]').filter((x: {path: string}) => x.path.endsWith('/release')).length)).toBe(1)
  const leases = await page.evaluate(() => JSON.parse(sessionStorage.getItem('buffer.calls') ?? '[]').filter((x: {path: string}) => x.path.endsWith('/lease')).length)
  expect(leases).toBe(1)
})

test('buffer recovery denial is terminal and never attaches or reacquires', async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const request = new Request(input, init)
      if (new URL(request.url).pathname.endsWith('/playback/buffer/lease')) {
        sessionStorage.setItem('denied.leases', String(Number(sessionStorage.getItem('denied.leases') ?? 0) + 1))
        return Response.json({ code: 'playback_stopped', message: 'Playback was stopped by an administrator.' }, { status: 409 })
      }
      return original(request)
    }
  })
  let mediaRequests = 0
  page.on('request', (request) => { if (request.url().includes('/stream/fixture-token/')) mediaRequests++ })
  await page.goto('/play/ch%3Aiptv%3A1')
  await expect(page.getByText('Playback was stopped by an administrator.', { exact: true })).toBeVisible()
  await page.waitForTimeout(1500)
  expect(mediaRequests).toBe(0)
  expect(await page.evaluate(() => sessionStorage.getItem('denied.leases'))).toBe('1')
})

test('lease client rejects stale seeks and releases exactly once', async ({ page }) => {
  const result = await page.evaluate(async () => {
    const modulePath = '/src/player/bufferSession.ts'
    const { BufferSession } = await import(modulePath)
    const original = window.fetch.bind(window)
    let index = 0
    window.fetch = async (input, init) => {
      const request = new Request(input, init)
      if (new URL(request.url).pathname.endsWith('/buffer/seek') && ++index === 1) {
        // Deliberately ignore abort to verify the sequence guard as well.
        await new Promise((resolve) => setTimeout(resolve, 100))
        return Response.json({ url: '/stale', window: {} })
      }
      return original(request)
    }
    const lease = await BufferSession.acquire({ live: true, mode: 'hls', url: `${location.origin}/api/v1/stream/fixture-token/index.m3u8` }, new AbortController().signal)
    const first = lease.seek('2026-11-01T10:00:00Z').then(() => 'stale', (error: Error) => error.name)
    await lease.seek(null)
    const stale = await first
    await Promise.all([lease.release(), lease.release()])
    return { stale, calls: JSON.parse(sessionStorage.getItem('buffer.calls') ?? '[]') }
  })
  expect(result.stale).toBe('AbortError')
  expect(result.calls.filter((entry: {path: string}) => entry.path.endsWith('/release'))).toHaveLength(1)
})
