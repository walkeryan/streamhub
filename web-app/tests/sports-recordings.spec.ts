import { expect, test } from '@playwright/test'
import { signIn } from './helpers'
import { fits } from './sports-helpers'

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    const item = { title_id: 'lib:plex-fixture:42', title: 'Championship library video', source_id: 'plex-fixture', source_name: 'My Plex library', sport: 'Soccer', originally_available_at: '2024-05-12', duration_ms: 3600000, audio_languages: [], audio_verified: false, language_hint: 'eng', kind: 'library_video', live: false }
    window.fetch = async (input, init) => {
      const request = new Request(input, init), url = new URL(request.url)
      if (url.pathname === '/api/v1/sports/events') return Response.json({ events: [], total: 0, limit: 100, offset: 0, sports: [], coverage: { eligible_channels: 0, channels_with_sports_schedule: 0 }, as_of: new Date().toISOString(), date: url.searchParams.get('date'), timezone: url.searchParams.get('timezone'), truncated: false })
      if (url.pathname === '/api/v1/sports/channels') return Response.json({ channels: [], total: 0, limit: 24, offset: 0, truncated: false })
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

test('library videos stay apart from live games, keep their exact identity and choose audio on the title page', async ({ page }) => {
  await signIn(page)
  await page.goto('/sports')
  const section = page.getByTestId('sports-recordings')
  await expect(section.getByRole('heading', { name: 'From your library' })).toBeVisible()
  await expect(page.getByTestId('sports-live')).not.toContainText('Championship library video')
  await expect(section).toContainText('Soccer · My Plex library · Released 2024-05-12')
  // The Sports screen says nothing about audio; the title page asks when the profile needs a choice.
  await expect(page.getByTestId('sports')).not.toContainText(/audio|verif/i)
  const unmatched = section.getByTestId('sports-recording').filter({ hasText: 'Unmatched metadata item' })
  await expect(unmatched).toContainText('Not playable')
  await expect(unmatched).toHaveAttribute('aria-disabled', 'true')
  await expect(unmatched).not.toHaveAttribute('role', 'button')
  await page.setViewportSize({ width: 390, height: 844 })
  await section.scrollIntoViewIfNeeded()
  expect(await fits(page)).toBe(true)
  await page.setViewportSize({ width: 1280, height: 720 })
  const open = section.getByRole('button', { name: 'Open Championship library video' })
  await open.hover()
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/title\/lib%3Aplex-fixture%3A42\?choose=1$/)
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByRole('dialog')).toContainText('My Plex library')
  await expect(page).not.toHaveURL(/\/play\//)
})

test('library videos share search, language and sport, never the day', async ({ page }) => {
  await signIn(page)
  await page.goto('/sports')
  await expect(page.getByTestId('sports-recording')).toHaveCount(2)
  await page.getByRole('button', { name: 'Tomorrow', exact: true }).click()
  await page.getByRole('button', { name: 'Next day', exact: true }).click()
  await expect(page.getByTestId('sports-recording')).toHaveCount(2)
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('recordings.query'))).not.toContain('date=')
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('recordings.query'))).not.toContain('status=')
  await page.getByTestId('sports-filters').click()
  await page.getByRole('textbox', { name: 'Search games, teams or channels' }).fill('missing')
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('recordings.query'))).toContain('q=missing')
  await page.getByRole('button', { name: 'Done', exact: true }).click()
  // Nothing matches: the section goes away instead of explaining itself.
  await expect(page.getByTestId('sports-recordings')).toHaveCount(0)
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await fits(page)).toBe(true)
})
