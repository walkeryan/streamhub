import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    const value = { mode: 'local_buffer', start: '2026-11-01T10:00:00Z', end: '2026-11-01T10:01:00Z', buffered_seconds: 60, target_seconds: 3600, recording_enabled: true }
    window.fetch = async (input, init) => {
      const request = new Request(input, init), url = new URL(request.url)
      const mode = sessionStorage.getItem('fixture.rewindMode')
      if (url.pathname === '/api/v1/playback/resolve' && mode === 'browser') {
        const { id } = await request.json()
        // The fixture worker does not implement byte ranges. The blob is genuinely seekable; the second URL is only buffered.
        const url = id === 'ch:iptv:1' ? URL.createObjectURL(await (await original('/appearance/sample.mp4')).blob()) : '/appearance/sample.mp4'
        return Response.json({ id, title: id, live: true, mode: 'direct', mime: 'video/mp4', url, buffer_available: false, resume_position_ms: 0 })
      }
      if (url.pathname === '/api/v1/playback/progress') return new Response(null, { status: 204 })
      if (url.pathname === '/api/v1/playback/resolve') return Response.json({ id: 'ch:iptv:1', title: 'Fixture buffer', live: true, mode: 'hls', url: `${location.origin}/api/v1/stream/fixture-token/index.m3u8`, buffer_available: true, resume_position_ms: 0 })
      if (url.pathname.startsWith('/api/v1/playback/buffer/')) {
        const body = await request.json()
        const records = JSON.parse(sessionStorage.getItem('buffer.calls') ?? '[]')
        records.push({ path: url.pathname, body }); sessionStorage.setItem('buffer.calls', JSON.stringify(records))
        if (url.pathname.endsWith('/lease') && mode === 'denied') {
          sessionStorage.setItem('denied.leases', String(Number(sessionStorage.getItem('denied.leases') ?? 0) + 1))
          return Response.json({code:'playback_stopped',message:'Playback was stopped by an administrator.'}, {status:409})
        }
        if (url.pathname.endsWith('/lease') && (mode === 'capacity' || mode === 'occupied')) {
          const code = mode === 'capacity' ? 'buffer_storage_full' : 'buffer_session_active'
          return Response.json({ code, message: 'Buffer unavailable.' }, { status: 409 })
        }
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
  await expect(controls).toContainText('1:00 available')
  const slider = controls.getByRole('slider', { name: 'Rewind watched channel' })
  await slider.hover()
  await expect(slider).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowLeft')
  await page.keyboard.press('Enter')
  await expect.poll(() => page.evaluate(() => JSON.parse(sessionStorage.getItem('buffer.calls') ?? '[]').filter((x: {path: string}) => x.path.endsWith('/seek')).at(-1)?.body.at)).toBe('2026-11-01T10:00:50.000Z')
  const bar = controls.getByTestId('scrub-track')
  const rect = (await bar.boundingBox())!
  await page.mouse.move(rect.x + rect.width * .65, rect.y + rect.height / 2)
  await page.mouse.down()
  await page.mouse.move(rect.x + rect.width * .25, rect.y + rect.height / 2)
  await expect(controls.getByTestId('scrub-bubble')).toContainText('behind live')
  await page.mouse.up()
  await expect.poll(() => page.evaluate(() => JSON.parse(sessionStorage.getItem('buffer.calls') ?? '[]').filter((x: {path: string}) => x.path.endsWith('/seek')).at(-1)?.body.at)).toMatch(/^2026-11-01T10:00:1[45]/)
  await page.getByRole('button', { name: 'Go Live', exact: true }).click()
  await expect.poll(() => page.evaluate(() => JSON.parse(sessionStorage.getItem('buffer.calls') ?? '[]').filter((x: {path: string}) => x.path.endsWith('/seek')).at(-1)?.body.at)).toBeNull()
  await page.getByRole('button', { name: 'Close player', exact: true }).click()
  await expect.poll(() => page.evaluate(() => JSON.parse(sessionStorage.getItem('buffer.calls') ?? '[]').filter((x: {path: string}) => x.path.endsWith('/release')).length)).toBe(1)
  const leases = await page.evaluate(() => JSON.parse(sessionStorage.getItem('buffer.calls') ?? '[]').filter((x: {path: string}) => x.path.endsWith('/lease')).length)
  expect(leases).toBe(1)
})

test('buffer recovery denial is terminal and never attaches or reacquires', async ({ page }) => {
  await page.evaluate(() => sessionStorage.setItem('fixture.rewindMode', 'denied'))
  let mediaRequests = 0
  page.on('request', (request) => { if (request.url().includes('/stream/fixture-token/')) mediaRequests++ })
  await page.goto('/play/ch%3Aiptv%3A1')
  await expect(page.getByText('Playback was stopped by an administrator.', { exact: true })).toBeVisible()
  await page.waitForTimeout(1500)
  expect(mediaRequests).toBe(0)
  expect(await page.evaluate(() => sessionStorage.getItem('denied.leases'))).toBe('1')
})

for (const mode of ['capacity', 'occupied']) {
  test(`optional ${mode} failure still opens the authorized live stream`, async ({ page }) => {
    await page.evaluate(value => sessionStorage.setItem('fixture.rewindMode', value), mode)
    const mediaRequest = page.waitForRequest(request => new URL(request.url()).pathname === '/api/v1/stream/fixture-token/index.m3u8')
    await page.goto('/play/ch%3Aiptv%3A1')
    await mediaRequest
    await expect(page.getByText('Server rewind is unavailable.', {exact:false})).toBeAttached()
  })
}

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

test('an authorized live tune rewinds decoded browser media immediately without a server lease', async ({ page }) => {
  await page.evaluate(() => sessionStorage.setItem('fixture.rewindMode', 'browser'))
  await page.goto('/play/ch%3Aiptv%3A1')
  const media = page.getByTestId('video')
  await expect.poll(() => media.evaluate(v => (v as HTMLVideoElement).readyState)).toBeGreaterThanOrEqual(2)
  await media.evaluate(el => { const v = el as HTMLVideoElement; v.pause(); v.currentTime = 3 })
  await page.mouse.move(320, 300)
  const timeline = page.getByTestId('local-buffer-controls')
  const slider = timeline.getByRole('slider', { name: 'Rewind watched channel' })
  const box = (await timeline.getByTestId('scrub-track').boundingBox())!
  await expect(slider).toHaveAttribute('aria-valuemax', '6')
  await page.mouse.click(box.x + box.width * .25, box.y + box.height / 2)
  await expect.poll(() => media.evaluate(v => (v as HTMLVideoElement).currentTime)).toBeCloseTo(1.5, 0)
  await timeline.getByRole('button', { name:'Go Live' }).click()
  await expect.poll(() => media.evaluate(v => (v as HTMLVideoElement).currentTime)).toBeGreaterThan(5)
  await page.evaluate(() => { history.pushState({}, '', '/play/ch%3Aiptv%3A2'); window.dispatchEvent(new PopStateEvent('popstate')) })
  await expect(page.getByRole('heading', {name:'ch:iptv:2', exact:true})).toBeAttached()
  await expect.poll(() => media.evaluate(v => (v as HTMLVideoElement).readyState)).toBeGreaterThanOrEqual(2)
  await expect.poll(() => media.evaluate(el => { const v = el as HTMLVideoElement; return v.buffered.length ? v.buffered.end(0) : 0 })).toBeGreaterThan(0)
  await expect(slider).toHaveAttribute('aria-disabled', 'true')
  await expect(timeline).toContainText('Building rewind window')
  expect(await page.evaluate(() => JSON.parse(sessionStorage.getItem('buffer.calls') ?? '[]').filter((x: { path: string }) => x.path.endsWith('/seek')))).toEqual([])
})
