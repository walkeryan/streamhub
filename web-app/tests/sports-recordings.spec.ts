import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    const item = { title_id: 'lib:plex-fixture:42', title: 'Championship library video', source_id: 'plex-fixture', source_name: 'My Plex library', sport: 'Soccer', originally_available_at: '2024-05-12', duration_ms: 3600000, audio_languages: [], audio_verified: false, language_hint: 'eng', kind: 'library_video', live: false }
    window.fetch = async (input, init) => {
      const request = new Request(input, init), url = new URL(request.url)
      if (url.pathname === '/api/v1/sports/events') return Response.json({ events: [], total: 0, limit: 40, offset: 0, sports: [], coverage: { eligible_channels: 0, channels_with_sports_schedule: 0 }, as_of: new Date().toISOString(), timezone: 'America/New_York', truncated: false })
      if (url.pathname === '/api/v1/sports/recordings') {
        sessionStorage.setItem('recordings.query', url.search)
        if (url.searchParams.get('q') === 'missing') return Response.json({ items: [], total: 0, limit: 40, offset: 0, truncated: false })
        return Response.json({ items: [item, { ...item, title_id: 'lib:wrong-source:9', title: 'Unmatched metadata item', originally_available_at: null }], total: 2, limit: 40, offset: 0, truncated: false })
      }
      if (url.pathname === '/api/v1/titles/lib%3Aplex-fixture%3A42' || decodeURIComponent(url.pathname) === '/api/v1/titles/lib:plex-fixture:42') return Response.json({ id: item.title_id, title: item.title, kind: 'movie', sources: [{ source_id: item.source_id, source_type: 'plex', name: item.source_name, playable_in_app: true, kind: 'vod' }], genres: ['Sports'] })
      if (url.pathname === '/api/v1/playback/options' && url.searchParams.get('id') === item.title_id) return Response.json({ id: item.title_id, preferred_languages: ['eng'], automatic_variant_id: null, selection_required: true, options: [{ variant_id: item.title_id, source_id: item.source_id, source_name: item.source_name, provider_label: item.title, audio_languages: [], default_audio_language: null, audio_verified: false, preferred_audio_available: false, kind: 'movie' }] })
      return original(request)
    }
  })
})

test('recordings stay separate from live events, retain exact identity and reuse audio choice', async ({ page }) => {
  await signIn(page)
  await page.goto('/sports')
  const section = page.getByTestId('sports-recordings')
  await expect(section).toContainText('On-demand library items, not live events')
  await expect(section).toContainText('Original release date: 2024-05-12')
  await expect(section).toContainText('Audio unverified · Metadata hint: English')
  await expect(section).not.toContainText('Verified audio: English')
  const unmatched = section.getByTestId('sports-recording').filter({ hasText: 'Unmatched metadata item' })
  await expect(unmatched).toContainText('No exact playable library match')
  await expect(unmatched.getByRole('button')).toHaveCount(0)
  await page.setViewportSize({ width: 390, height: 844 })
  await section.scrollIntoViewIfNeeded()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: '/tmp/streamhub-sports-recordings-mobile.png' })
  await page.setViewportSize({ width: 1280, height: 720 })
  const open = section.getByRole('button', { name: 'View recording and audio' })
  await open.hover()
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/title\/lib%3Aplex-fixture%3A42\?choose=1$/)
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByRole('dialog')).toContainText('My Plex library')
  await expect(page).not.toHaveURL(/\/play\//)
})

test('recordings share search/language/sport but do not inherit guide date or status', async ({ page }) => {
  await signIn(page)
  await page.goto('/sports')
  await expect(page.getByTestId('sports-recording')).toHaveCount(2)
  await page.getByRole('button', { name: 'Upcoming', exact: true }).click()
  await page.getByTestId('sports-filters').click()
  await page.getByRole('button', { name: 'Next day', exact: true }).click()
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('recordings.query'))).not.toContain('date=')
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('recordings.query'))).not.toContain('status=')
  await page.getByRole('textbox', { name: 'Search sports programmes or channels' }).fill('missing')
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('recordings.query'))).toContain('q=missing')
  await page.getByRole('button', { name: 'Show events', exact: true }).click()
  await expect(page.getByTestId('sports-recordings')).toContainText('No matching library sports videos')
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
