import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const source = { source_id: 'iptv', source_type: 'xtream', name: 'My library', playable_in_app: true }
    const movie = { id: 'tmdb:movie:140607', kind: 'movie', title: 'Star Wars: The Force Awakens', year: 2015, sources: [source] }
    const missing = { id: 'tmdb:movie:missing', kind: 'movie', title: 'Star Wars: Unavailable fixture', sources: [] }
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const req = new Request(input, init), url = new URL(req.url)
      if (url.pathname === '/api/v1/search') {
        const q = url.searchParams.get('q') ?? ''
        if (q === 'slow') await new Promise((r) => setTimeout(r, 1200))
        return Response.json({ results: q === 'slow' ? [{ ...movie, title: 'Stale slow result' }] : [movie, missing, { ...missing, id: 'parody', title: 'Space parody' }], discovery_note: 'Metadata discovery; availability checked separately.' })
      }
      if (url.pathname.startsWith('/api/v1/titles/tmdb')) return Response.json(url.pathname.includes('missing') ? missing : movie)
      if (url.pathname === '/api/v1/playback/options') return Response.json({ id: movie.id, preferred_languages: ['eng'], automatic_variant_id: null, selection_required: true, options: [{ variant_id: 'lib:iptv:fr', source_id: 'iptv', source_name: 'My library', provider_label: 'FR-NETFLIX-force-awakens', audio_languages: [], audio_verified: false, audio_evidence: 'unknown', language_hint: 'fra', preferred_audio_available: false }] })
      if (url.pathname === '/api/v1/playback/resolve') {
        sessionStorage.setItem('test.resolve', await req.text())
        return Response.json({ mode: 'handoff', id: movie.id, handoff: { web_url: 'https://example.invalid', app_name: 'Fixture' } })
      }
      return original(req)
    }
  })
})

test('canonical ranking, unavailable discovery and Back/refresh preserve query', async ({ page }) => {
  await signIn(page)
  await page.goto('/search')
  await page.getByLabel('Search titles and channels').fill('Star Wars')
  await expect(page.getByTestId('group-relevance')).toBeVisible()
  const results = page.getByTestId('group-relevance').getByRole('link')
  await expect(results.first()).toHaveAccessibleName('Star Wars: The Force Awakens')
  await expect(page.getByText('FR-NETFLIX-force-awakens')).toHaveCount(0)
  await page.getByRole('link', { name: 'Star Wars: Unavailable fixture', exact: true }).click()
  await expect(page.getByTestId('title-play')).toHaveCount(0)
  await expect(page.getByText('Not available from your sources.')).toBeVisible()
  await page.goBack()
  await expect(page.getByLabel('Search titles and channels')).toHaveValue('Star Wars')
  await page.reload()
  await expect(page.getByLabel('Search titles and channels')).toHaveValue('Star Wars')
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByTestId('group-relevance')).toBeVisible()
  await page.screenshot({ path: '/tmp/streamhub-discovery-mobile.png' })
})

test('unknown audio requires deliberate variant confirmation; no silent French resolve', async ({ page }) => {
  await signIn(page)
  await page.goto('/title/tmdb%3Amovie%3A140607')
  await expect(page.getByTestId('title-play')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog')).toContainText('Audio unverified')
  expect(await page.evaluate(() => sessionStorage.getItem('test.resolve'))).toBeNull()
  await expect(page.getByRole('button', { name: 'Cancel', exact: true })).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowUp')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog')).toContainText('Audio language is unverified.')
  await expect(page.getByRole('button', { name: 'Back to sources', exact: true })).toHaveAttribute('data-focused', 'true')
  await expect(page.locator('.fx[data-focused="true"]')).toHaveCount(1)
  expect(await page.evaluate(() => document.activeElement === document.querySelector('.fx[data-focused="true"]'))).toBe(true)
  await page.waitForTimeout(250)
  await page.screenshot({ path: '/tmp/streamhub-audio-confirmation.png' })
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('title-play')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  await page.getByRole('button', { name: /My library · Audio unverified/ }).click()
  await page.getByRole('button', { name: 'Play this source anyway' }).click()
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('test.resolve'))).not.toBeNull()
  expect(JSON.parse((await page.evaluate(() => sessionStorage.getItem('test.resolve')))!)).toMatchObject({ variant_id: 'lib:iptv:fr', allow_unverified_audio: true })
  expect(page.url()).not.toContain('allow_audio=')
  await page.reload()
  await expect.poll(async () => JSON.parse((await page.evaluate(() => sessionStorage.getItem('test.resolve')))!)).toMatchObject({ variant_id: 'lib:iptv:fr', allow_unverified_audio: false })
})

test('late search responses cannot replace a newer query', async ({ page }) => {
  await signIn(page)
  await page.goto('/search')
  const input = page.getByLabel('Search titles and channels')
  await input.fill('slow')
  await page.waitForTimeout(500)
  await input.fill('Star Wars')
  await expect(page.getByRole('link', { name: 'Star Wars: The Force Awakens', exact: true })).toBeVisible()
  await page.waitForTimeout(1400)
  await expect(page.getByText('Stale slow result')).toHaveCount(0)
})

test('search state is isolated when switching profiles on the same URL', async ({ page }) => {
  await signIn(page)
  await page.goto('/search')
  await page.getByLabel('Search titles and channels').fill('Star Wars')
  await expect(page.getByTestId('group-relevance')).toBeVisible()
  await page.evaluate(async () => {
    const path = '/src/store/session.ts', fixturePath = '/src/mocks/fixtures.ts'
    const { useSession } = await import(/* @vite-ignore */ path)
    const { profiles, DISCLAIMER } = await import(/* @vite-ignore */ fixturePath)
    await fetch('/api/v1/disclaimer/accept', { method: 'POST', headers: { authorization: 'Bearer mock-access-p1' } })
    useSession.getState().setSession('mock-access-p1', 'mock-refresh-p1', { ...profiles[0], disclaimer_accepted_version: DISCLAIMER.version })
  })
  await expect(page.getByLabel('Search titles and channels')).toHaveValue('')
  await expect(page.getByTestId('group-relevance')).toHaveCount(0)
})

test('direct playback audio-selection errors stop reconnecting and offer source details', async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const req = new Request(input, init)
      if (new URL(req.url).pathname === '/api/v1/playback/resolve') {
        sessionStorage.setItem('resolve.count', String(Number(sessionStorage.getItem('resolve.count') ?? 0) + 1))
        return Response.json({ code: 'audio_selection_required', message: 'Choose verified audio' }, { status: 409 })
      }
      return original(req)
    }
  })
  await signIn(page)
  await page.goto('/play/tmdb%3Amovie%3A140607')
  await expect(page.getByTestId('player-error')).toContainText('Choose an audio language and source')
  await page.waitForTimeout(3500)
  expect(await page.evaluate(() => sessionStorage.getItem('resolve.count'))).toBe('1')
  await page.getByRole('button', { name: 'Choose audio / source', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('Audio unverified')
})


test('audio consent cannot carry across a profile switch, even back to its owner', async ({ page }) => {
  await signIn(page)
  const result = await page.evaluate(async () => {
    const consentPath = '/src/player/audioConsent.ts', sessionPath = '/src/store/session.ts', fixturePath = '/src/mocks/fixtures.ts'
    const { grantAudioConsent, hasAudioConsent } = await import(/* @vite-ignore */ consentPath)
    const { useSession } = await import(/* @vite-ignore */ sessionPath)
    const { profiles, DISCLAIMER } = await import(/* @vite-ignore */ fixturePath)
    const token = grantAudioConsent('movie', 'variant')
    const before = hasAudioConsent(token, 'movie', 'variant')
    useSession.getState().setSession('mock-access-p1', 'mock-refresh-p1', { ...profiles[0], disclaimer_accepted_version: DISCLAIMER.version })
    const other = hasAudioConsent(token, 'movie', 'variant')
    useSession.getState().setSession('mock-access-p2', 'mock-refresh-p2', { ...profiles[1], disclaimer_accepted_version: DISCLAIMER.version })
    return { before, other, returned: hasAudioConsent(token, 'movie', 'variant') }
  })
  expect(result).toEqual({ before: true, other: false, returned: false })
})

test('channel title details Play bypasses the VOD audio chooser', async ({ page }) => {
  await signIn(page)
  const id = await page.evaluate(async () => {
    const path = '/src/mocks/fixtures.ts'
    return (await import(/* @vite-ignore */ path)).channels[0].id
  })
  await page.goto(`/title/${encodeURIComponent(id)}`)
  await expect(page.getByTestId('title-play')).toHaveText('Play')
  await page.getByTestId('title-play').click()
  await expect(page).toHaveURL(/\/play\//)
  await expect(page.getByRole('dialog')).toHaveCount(0)
})
