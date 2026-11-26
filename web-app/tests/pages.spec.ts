import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('sources: streaming service toggle and Test link opens the service web_url', async ({ page }) => {
  await signIn(page)
  await page.goto('/sources')
  const hulu = page.getByRole('switch', { name: 'Hulu', exact: true })
  await expect(hulu).toHaveAttribute('aria-checked', 'false')
  await hulu.click()
  await expect(hulu).toHaveAttribute('aria-checked', 'true')
  const [popup] = await Promise.all([
    page.waitForEvent('popup'),
    page.getByLabel('Test link for Hulu').click(),
  ])
  expect(popup.url()).toContain('hulu.com')
})

test('sources: M3U form validates before sending', async ({ page }) => {
  await signIn(page)
  await page.goto('/sources')
  await page.getByTestId('add-m3u').click()
  await page.getByRole('button', { name: 'Add source' }).click()
  await expect(page.getByText('Give the source a name')).toBeVisible()
  await expect(page.getByText('Add a playlist address or choose a file')).toBeVisible()
})

test('pairing: approve a TV code', async ({ page }) => {
  await signIn(page)
  await page.goto('/pair')
  await page.getByLabel('TV code').fill('abc12')
  await expect(page.getByTestId('pair-review')).toHaveAttribute('aria-disabled', 'true')
  await page.getByLabel('TV code').fill('abc123')
  // Entering the code approves nothing: the next screen says what is asking, and is where it is approved.
  await page.getByTestId('pair-review').click()
  await expect(page.getByTestId('pair-review-panel')).toContainText('ABC123')
  await page.getByTestId('pair-approve').click()
  await expect(page.getByTestId('pair-done')).toBeVisible()
})

test('title detail: availability attribution and hand-off labelling', async ({ page }) => {
  await signIn(page)
  await page.goto('/title/' + encodeURIComponent('tmdb:movie:105')) // Netflix only
  await expect(page.getByTestId('title-play')).toContainText('Open in app')
  await expect(page.getByTestId('justwatch-attribution')).toContainText('Availability data by JustWatch')
})

test('a poster still arriving leaves its tile blank, then fades in whole: it never paints top to bottom', async ({ page }) => {
  // The Movies grid's posters point at an address this test serves, held until it lets them through, as a slow
  // connection would hold them (the fixture's own posters are inline and arrive at once).
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const path = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, location.href).pathname
      const res = await original(input, init)
      if (!path.startsWith('/api/v1/browse/movies')) return res
      let n = 0
      const slow = (value: unknown): unknown => Array.isArray(value) ? value.map(slow)
        : value && typeof value === 'object' ? Object.fromEntries(Object.entries(value).map(([k, v]) => [k, (k === 'poster' || k === 'backdrop') && typeof v === 'string' ? `/slow-poster/${n++}.png` : slow(v)]))
        : value
      return Response.json(slow(await res.json()), { status: res.status })
    }
  })
  let release: () => void = () => undefined
  const gate = new Promise<void>((resolve) => { release = resolve })
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64')
  // On the context: the mock server's service worker makes the request, which a page route never sees.
  await page.context().route('**/slow-poster/**', async (route) => { await gate; await route.fulfill({ body: png, contentType: 'image/png' }) })
  await signIn(page)
  await page.goto('/browse/movies')
  // The first tile on screen (tiles further down are lazy and not asked for until scrolled to).
  const art = page.getByTestId('tile').first().locator('img.art')
  await expect(art).toHaveAttribute('src', /^\/slow-poster\//)
  await expect(art).toHaveAttribute('data-loaded', 'false')
  await expect(art).toHaveCSS('opacity', '0')
  release()
  await expect(art).toHaveAttribute('data-loaded', 'true')
  await expect(art).toHaveCSS('opacity', '1')
})
