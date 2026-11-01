import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const req = new Request(input, init), url = new URL(req.url)
      if (url.pathname === '/api/v1/playback/progress') {
        const rows = JSON.parse(sessionStorage.getItem('audio.progress') ?? '[]'); rows.push(await req.json()); sessionStorage.setItem('audio.progress', JSON.stringify(rows))
        return new Response(null, { status: 204 })
      }
      if (url.pathname === '/api/v1/playback/options') {
        const id = url.searchParams.get('id') ?? ''
        if (!id.startsWith('ch:') && !id.startsWith('prog:') && id !== 'tmdb:movie:multi') return original(req)
        const state = window as unknown as { holdAudio?: boolean; audioPending?: boolean; releaseAudio?: () => void }
        if (state.holdAudio && !id.endsWith('1001')) { state.audioPending = true; await new Promise<void>((resolve) => { state.releaseAudio = resolve }); state.holdAudio = false }
        const automatic = id.endsWith('1001') || id.endsWith(':verified')
        const multi = id.endsWith(':multi')
        const variant = id.startsWith('ch:') ? id : id.startsWith('tmdb:') ? 'lib:fixture:multi' : 'ch:fixture:programme'
        const option = { variant_id: variant, source_id: id.split(':')[1], source_name: 'Fixture feed', provider_label: 'EN — Selected channel', kind: 'channel', audio_languages: multi ? ['eng', 'fra'] : automatic ? ['eng'] : [], default_audio_language: multi ? 'fra' : automatic ? 'eng' : null, audio_verified: automatic || multi, audio_evidence: automatic || multi ? 'audio_track_metadata' : 'unknown', preferred_audio_available: automatic, language_hint: 'eng' }
        return Response.json({ id, preferred_languages: ['eng'], options: [option], automatic_variant_id: automatic ? variant : null, selection_required: !automatic })
      }
      if (decodeURIComponent(url.pathname) === '/api/v1/titles/tmdb:movie:multi') return Response.json({ id: 'tmdb:movie:multi', kind: 'movie', title: 'Audio fixture', sources: [{ source_id: 'fixture', source_type: 'xtream', name: 'Fixture feed', playable_in_app: true }] })
      if (url.pathname === '/api/v1/playback/resolve') {
        const body = await req.json()
        const rows = JSON.parse(sessionStorage.getItem('audio.resolves') ?? '[]')
        rows.push(body); sessionStorage.setItem('audio.resolves', JSON.stringify(rows))
        return Response.json({ id: body.id, mode: 'direct', live: !body.rewind_at && !body.catchup_programme_id, url: '/mock/sample.webm', mime: 'video/webm', title: 'Fixture channel', duration_ms: 3600000 })
      }
      if (url.pathname === '/api/v1/playback/rewind') return Response.json({ mode: 'provider_archive', advertised_start: '2026-11-01T05:30:00Z', advertised_end: '2026-11-01T06:30:00Z', verified: false, recording_enabled: false, granularity_seconds: 60, reason: 'Fixture' })
      if (url.pathname === '/api/v1/sports/events') {
        const now = Date.now(), start = new Date(now - 60000).toISOString(), stop = new Date(now + 3600000).toISOString()
        return Response.json({ as_of: new Date(now).toISOString(), date: url.searchParams.get('date'), timezone: 'America/New_York', total: 1, limit: 40, offset: 0, sports: [], coverage: { eligible_channels: 1, channels_with_sports_schedule: 1 }, truncated: false, events: [{ id: 'event', title: 'Fixture match', sport: 'Soccer', start, stop, status: 'current', replay: false, live_evidence: false, grouping_evidence: 'same_source_programme', feeds: [{ channel_id: 'ch:fixture:unknown', channel_name: 'Fixture channel', source_id: 'fixture', source_name: 'Fixture feed', programme_id: 'p:1', language: 'eng', language_verified: false }] }] })
      }
      return original(req)
    }
  })
})

const bodies = async (page: import('@playwright/test').Page) => page.evaluate(() => JSON.parse(sessionStorage.getItem('audio.resolves') ?? '[]') as Array<Record<string, unknown>>)

test('unknown direct channel starts no media before explicit remote confirmation and reload requires consent', async ({ page }) => {
  await signIn(page)
  await page.goto('/play/ch%3Afixture%3Aunknown')
  await expect(page.getByRole('dialog')).toContainText('Audio unverified')
  await expect(page.getByRole('dialog')).toContainText('Provider hint: English')
  expect(await bodies(page)).toEqual([])
  await expect(page.getByTestId('video')).not.toHaveAttribute('src', /.+/)
  await expect(page.getByRole('button', { name: 'Cancel playback', exact: true })).toHaveAttribute('data-focused', 'true')
  await page.waitForTimeout(250)
  await page.screenshot({ path: '/tmp/streamhub-live-audio.png' })
  await page.keyboard.press('ArrowUp'); await page.keyboard.press('Enter')
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  expect((await bodies(page))[0]).toMatchObject({ id: 'ch:fixture:unknown', variant_id: 'ch:fixture:unknown', allow_unverified_audio: true })
  await page.reload()
  await expect(page.getByRole('dialog')).toContainText('Audio unverified')
  expect(await bodies(page)).toHaveLength(1)
  await page.keyboard.press('Escape')
  await expect(page).toHaveURL(/\/live$/)
  await expect(page.getByLabel('Search channels or number')).toBeFocused()
})

test('pending zap stops the old media and ignores repeat zap keys; Back cancels without a new resolve', async ({ page }) => {
  await signIn(page)
  await page.goto('/live')
  await page.getByTestId('channel-row').first().click()
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  expect(await bodies(page)).toHaveLength(1)
  const nextChannel = await page.evaluate(async () => { const path = '/src/store/ui.ts'; const { useUi } = await import(/* @vite-ignore */ path); return useUi.getState().channelList.channels[1].id as string })
  await page.evaluate(() => { (window as unknown as { holdAudio: boolean }).holdAudio = true })
  await page.evaluate(() => { for (const key of ['ArrowUp', 'ArrowUp']) window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true })) })
  await expect.poll(() => page.evaluate(() => (window as unknown as { audioPending?: boolean }).audioPending)).toBe(true)
  await expect(page.getByRole('dialog')).toContainText('Checking audio evidence')
  await expect(page).toHaveURL(new RegExp(encodeURIComponent(nextChannel)))
  const pendingUrl = page.url()
  await page.keyboard.press('ArrowUp'); await page.keyboard.press('ArrowDown')
  expect(page.url()).toBe(pendingUrl)
  expect(await bodies(page)).toHaveLength(1)
  await expect(page.getByTestId('video')).not.toHaveAttribute('src', /.+/)
  await page.keyboard.press('Escape')
  await expect(page).toHaveURL(/\/live$/)
  await page.evaluate(() => (window as unknown as { releaseAudio: () => void }).releaseAudio())
  expect(await bodies(page)).toHaveLength(1)
})

test('verified English track with French default is explicit, not automatic English', async ({ page }) => {
  await signIn(page)
  await page.goto('/play/ch%3Afixture%3Amulti')
  await expect(page.getByRole('dialog')).toContainText('Verified tracks: English, French · Default audio: French')
  expect(await bodies(page)).toEqual([])
  await page.getByRole('button', { name: /Play Fixture feed/ }).click()
  await expect.poll(() => bodies(page)).toHaveLength(1)
  expect((await bodies(page))[0].allow_unverified_audio).toBe(true)
})

test('catch-up intent is retained and changing archive position requires a new exact-scope confirmation', async ({ page }) => {
  await signIn(page)
  await page.goto('/play/ch%3Afixture%3Aunknown?source=fixture&catchup=prog%3A77')
  await page.getByRole('button', { name: /Play Fixture feed/ }).click()
  await expect.poll(() => bodies(page)).toHaveLength(1)
  expect((await bodies(page))[0]).toMatchObject({ source_id: 'fixture', catchup_programme_id: 'prog:77', rewind_at: null, allow_unverified_audio: true })
  await page.getByRole('button', { name: 'Try 1 minutes ago. Left and Right adjust, Select plays.', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  expect(await bodies(page)).toHaveLength(1)
  await page.getByRole('button', { name: /Play Fixture feed/ }).click()
  await expect.poll(() => bodies(page)).toHaveLength(2)
  expect((await bodies(page))[1]).toMatchObject({ source_id: 'fixture', catchup_programme_id: null, rewind_at: '2026-11-01T06:29:00.000Z', variant_id: 'ch:fixture:unknown', allow_unverified_audio: true })
  await page.getByRole('button', { name: 'Go Live', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  expect(await bodies(page)).toHaveLength(2)
})

test('Sports explicit labelled feed selection reuses consent without a second prompt', async ({ page }) => {
  await signIn(page)
  await page.goto('/sports')
  await page.getByRole('button', { name: 'View feeds for Fixture match', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('Choosing Watch explicitly accepts')
  await page.getByRole('button', { name: 'Watch Fixture channel', exact: true }).click()
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect((await bodies(page))[0]).toMatchObject({ variant_id: 'ch:fixture:unknown', allow_unverified_audio: true })
})


test('Title source chooser distinguishes verified tracks from the actual default audio', async ({ page }) => {
  await signIn(page)
  await page.goto('/title/tmdb%3Amovie%3Amulti')
  await page.getByTestId('title-play').click()
  await expect(page.getByRole('dialog')).toContainText('Verified tracks: English, French · Default audio: French')
  expect(await bodies(page)).toEqual([])
})

test('switching profiles never inherits live-channel consent or starts an unknown stream', async ({ page }) => {
  await signIn(page)
  await page.goto('/play/ch%3Afixture%3Aunknown')
  await page.getByRole('button', { name: /Play Fixture feed/ }).click()
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  await page.evaluate(async () => {
    await fetch('/api/v1/disclaimer/accept', { method: 'POST', headers: { authorization: 'Bearer mock-access-p1' } })
    const path = '/src/store/session.ts', fixturePath = '/src/mocks/fixtures.ts'
    const { useSession } = await import(/* @vite-ignore */ path)
    const { profiles, DISCLAIMER } = await import(/* @vite-ignore */ fixturePath)
    useSession.getState().setSession('mock-access-p1', 'mock-refresh-p1', { ...profiles[0], disclaimer_accepted_version: DISCLAIMER.version })
  })
  await expect(page.getByRole('dialog')).toContainText('Audio unverified')
  expect(await bodies(page)).toHaveLength(1)
})

test('programme entry retains programme identity while explicitly choosing its channel variant', async ({ page }) => {
  await signIn(page)
  await page.goto('/play/prog%3A77')
  await expect(page.getByRole('dialog')).toContainText('Audio unverified')
  expect(await bodies(page)).toEqual([])
  await page.getByRole('button', { name: /Play Fixture feed/ }).click()
  await expect.poll(() => bodies(page)).toHaveLength(1)
  expect((await bodies(page))[0]).toMatchObject({ id: 'prog:77', variant_id: 'ch:fixture:programme', allow_unverified_audio: true })
})

test('optional inline preview never starts unknown audio from an English provider hint', async ({ page }) => {
  await signIn(page)
  await page.goto('/live')
  await expect(page.getByTestId('channel-row').nth(1)).toBeVisible()
  await page.getByRole('button', { name: 'Preview off', exact: true }).click()
  await page.evaluate(async () => { const path = '/src/nav/spatial.ts'; const { setFocus } = await import(/* @vite-ignore */ path); await setFocus('live::ch:s_xt:1001') })
  await page.keyboard.press('ArrowDown')
  await expect(page.getByTestId('channel-row').nth(1)).toHaveAttribute('data-focused', 'true')
  await expect(page.getByTestId('live-preview')).toContainText('Audio requires confirmation')
  expect((await bodies(page)).filter((body) => !String(body.id).endsWith('1001'))).toEqual([])
  await expect(page.getByTestId('live-preview').locator('video')).not.toHaveAttribute('src', /.+/)
})


test('approved playback reports progress without stop reports on ordinary player rerenders', async ({ page }) => {
  await signIn(page)
  await page.goto('/play/ch%3Afixture%3Averified')
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  await page.evaluate(() => sessionStorage.removeItem('audio.progress'))
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('i')
  await page.keyboard.press('ArrowRight')
  const rows = await page.evaluate(() => JSON.parse(sessionStorage.getItem('audio.progress') ?? '[]') as Array<{state:string}>)
  expect(rows.filter((row) => row.state === 'stopped')).toEqual([])
})
