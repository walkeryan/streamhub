import { expect, test, type Page } from '@playwright/test'
import { chooseLiveOption, closeLiveOptions, openLiveOptions, signIn } from './helpers'

type Document = Record<string, unknown>
async function backend(page: Page, documents: Record<string, Document>) {
  await page.exposeFunction('livePrefsBackend', (id: string, method: string, body: Document | null) => {
    if (method === 'PUT') documents[id] = body!
    return documents[id] ?? {}
  })
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const req = new Request(input, init)
      const match = new URL(req.url).pathname.match(/^\/api\/v1\/profiles\/([^/]+)\/prefs$/)
      if (match) {
        const state = window as unknown as { livePrefsBackend: (id: string, method: string, body: Document | null) => Promise<Document> }
        return Response.json(await state.livePrefsBackend(match[1], req.method, req.method === 'PUT' ? await req.json() : null))
      }
      return original(req)
    }
  })
}

test('US country and English are independent in channels, guide, and unknown-region access', async ({ page }) => {
  await signIn(page)
  await page.evaluate(async () => {
    const path = '/src/mocks/fixtures.ts'
    const { channels } = await import(/* @vite-ignore */ path)
    const base = channels[0]
    channels.splice(0, channels.length, ...[
      { id: 'us-en', name: 'US English', country: 'US', language: 'eng' },
      { id: 'us-es', name: 'US Spanish', country: 'US', language: 'spa' },
      { id: 'gb-en', name: 'UK English', country: 'GB', language: 'eng' },
      { id: 'unknown', name: 'Unknown English', country: null, language: 'eng' },
    ].map((c, i) => ({ ...base, ...c, number: i + 1, group: c.country ?? 'Unknown' })))
  })
  await page.getByRole('link', { name: 'Live TV', exact: true }).click()
  // Region and language are in Options; the list under the sheet follows each choice as it is made.
  const options = await openLiveOptions(page)
  await expect(options.getByRole('button', { name: 'United States', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('channel-row')).toHaveCount(2)
  await options.getByRole('button', { name: 'English', exact: true }).click()
  await expect(page.getByTestId('channel-row')).toHaveCount(1)
  await expect(page.getByTestId('channel-row')).toContainText('US English')
  await options.getByRole('button', { name: 'All regions', exact: true }).click()
  await expect(page.getByTestId('channel-row')).toHaveCount(3)
  await expect(page.getByTestId('channel-row').filter({ hasText: 'UK English' })).toHaveCount(1)
  await options.getByRole('button', { name: 'Unknown region', exact: true }).click()
  await expect(page.getByTestId('channel-row')).toHaveCount(1)
  await expect(page.getByTestId('channel-row')).toContainText('Unknown English')
  await options.getByRole('button', { name: 'All languages', exact: true }).click()
  await options.getByRole('button', { name: 'United States', exact: true }).click()
  await expect(page.getByTestId('channel-row')).toHaveCount(2)
  await closeLiveOptions(page)
  await page.getByRole('button', { name: 'Guide', exact: true }).click()
  await expect(page.getByTestId('guide').getByRole('button', { name: /^\d+ US (English|Spanish)$/ })).toHaveCount(2)
  await expect(page.getByTestId('guide').getByRole('button', { name: '3 UK English', exact: true })).toHaveCount(0)
  await expect(page.locator('video')).toHaveCount(0)
  await page.getByRole('button', { name: 'Channels', exact: true }).click()
  await expect(page.getByTestId('channel-row')).toHaveCount(2)
})

test('live region and filters survive reload and another device without replacing other preferences', async ({ page, browser }) => {
  const documents: Record<string, Document> = { p2: { unrelated: { preserve: true } } }
  await backend(page, documents)
  await signIn(page)
  await page.getByRole('link', { name: 'Live TV', exact: true }).click()
  // All languages keeps more than one region in the list, so the region row stays on offer.
  await chooseLiveOption(page, 'All regions', 'All languages')
  const groups = (on: Page) => on.getByRole('group', { name: 'Channel groups', exact: true })
  await groups(page).getByRole('button', { name: 'Favorites', exact: true }).click()
  await expect.poll(() => documents.p2['liveFilters.v1']).toEqual({ language: 'all', country: 'all', source: '', group: '', favorites: true })
  // All, Favorites and the groups are one row of choices: a group replaces Favorites.
  await groups(page).getByRole('button', { name: 'Sports', exact: true }).click()
  await expect.poll(() => documents.p2['liveFilters.v1']).toEqual({ language: 'all', country: 'all', source: '', group: 'g1', favorites: false })
  expect(documents.p2.unrelated).toEqual({ preserve: true })
  await page.reload()
  const options = await openLiveOptions(page)
  await expect(options.getByRole('button', { name: 'All regions', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(options.getByRole('button', { name: 'All languages', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await closeLiveOptions(page)
  await expect(groups(page).getByRole('button', { name: 'Favorites', exact: true })).toHaveAttribute('aria-pressed', 'false')
  await expect(groups(page).getByRole('button', { name: 'Sports', exact: true })).toHaveAttribute('aria-pressed', 'true')
  const context = await browser.newContext(), other = await context.newPage()
  await backend(other, documents)
  await signIn(other)
  await other.getByRole('link', { name: 'Live TV', exact: true }).click()
  await expect((await openLiveOptions(other)).getByRole('button', { name: 'All regions', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await closeLiveOptions(other)
  await expect(groups(other).getByRole('button', { name: 'Sports', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await context.close()
})
