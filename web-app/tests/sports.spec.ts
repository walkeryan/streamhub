import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

for (const empty of [false, true]) {
  test(empty ? 'missing sports guide shows honest empty state' : 'sports filters and bounded pages watch the matching channel', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (e) => errors.push(e.message))
    await signIn(page)
    await page.evaluate((empty) => {
      const original = window.fetch
      window.fetch = async (input, init) => {
        const url = new URL(input instanceof Request ? input.url : String(input), location.href)
        if (url.pathname !== '/api/v1/sports/events') return original(input, init)
        const now = Date.now()
        let items = Array.from({ length: empty ? 0 : 5001 }, (_, i) => ({ id: `fixture-${i}`, sport: i % 2 ? 'Tennis' : 'Basketball', programme_id: `prog:${i}`, title: `Fixture programme ${i}`, start: new Date(now - 60000).toISOString(), stop: new Date(now + 600000).toISOString(), channel_id: `ch:fixture:${i}`, channel_name: `Fixture channel ${i}`, channel_number: i, source_name: 'Test feed' }))
        const q = url.searchParams.get('q')?.toLowerCase() ?? ''
        items = items.filter((x) => x.title.toLowerCase().includes(q) || x.channel_name.toLowerCase().includes(q))
        const sports = ['Basketball', 'Tennis'].map((name) => ({ name, count: items.filter((x) => x.sport === name).length })).filter((x) => x.count)
        const sport = url.searchParams.get('sport')
        if (sport) items = items.filter((x) => x.sport === sport)
        const offset = Number(url.searchParams.get('offset') ?? 0)
        const limit = Number(url.searchParams.get('limit') ?? 40)
        return new Response(JSON.stringify({ as_of: new Date(now).toISOString(), timezone: 'America/New_York', date: url.searchParams.get('date'), truncated: false, events: items.slice(offset, offset + limit).map((item) => ({ ...item, status: 'current', replay: false, live_evidence: false, grouping_evidence: 'same_source_programme', feeds: [{ ...item, source_id: 'fixture', language: 'eng', language_verified: false }] })), total: items.length, offset, limit, sports, coverage: { eligible_channels: 52366, channels_with_sports_schedule: empty ? 0 : 7572 } }), { headers: { 'content-type': 'application/json' } })
      }
    }, empty)
    await page.getByRole('link', { name: 'Sports', exact: true }).click()
    await expect(page).toHaveURL(/\/sports$/)
    if (empty) {
      await expect(page.getByText('No matching sports listings', { exact: true })).toBeVisible()
      await expect(page.getByRole('status').filter({ hasText: 'eligible channels' })).toContainText('0 of 52,366')
      await expect(page.getByText(/No listings does not mean no sport is airing/)).toBeVisible()
      await expect(page.getByRole('button', { name: 'Next', exact: true })).toHaveAttribute('aria-disabled', 'true')
      await page.getByTestId('sports-filters').click()
      await page.getByRole('button', { name: 'All languages', exact: true }).click()
      await expect(page.getByText('No matching sports listings', { exact: true })).toBeVisible()
      return
    }
    await expect(page.getByTestId('sports-listing')).toHaveCount(40)
    await expect(page.getByRole('status').filter({ hasText: 'eligible channels' })).toContainText('7,572 of 52,366')
    await page.getByRole('button', { name: 'Next', exact: true }).click()
    await expect(page.getByTestId('sports-listing').first()).toContainText('Fixture programme 40')
    await page.getByTestId('sports-filters').click()
    await expect(page.getByRole('dialog')).toContainText('replays, highlights and studio shows')
    await page.getByRole('button', { name: /^Tennis ·/ }).click()
    await expect(page.getByTestId('sports-listing').first()).toContainText('Fixture programme 1')
    await page.getByLabel('Search sports programmes or channels').fill('4999')
    await expect(page.getByTestId('sports-listing')).toHaveCount(1)
    await expect(page.getByTestId('sports-listing')).toContainText('Fixture programme 4999')
    await page.getByRole('button', { name: 'Show events', exact: true }).click()
    await page.screenshot({ path: '/tmp/streamhub-sports-desktop.png' })
    await page.setViewportSize({ width: 390, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: '/tmp/streamhub-sports-mobile.png' })
    expect(errors).toEqual([])
    await page.getByRole('button', { name: 'View feeds for Fixture programme 4999', exact: true }).click()
    await page.getByRole('button', { name: 'Watch Fixture channel 4999', exact: true }).click()
    await expect(page).toHaveURL(/\/play\/ch%3Afixture%3A4999(?:\?|$)/)
  })
}
