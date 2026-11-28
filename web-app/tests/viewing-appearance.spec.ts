import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'
import { backend, type Document } from './profilePrefsFixture'

async function appearance(page: Page) {
  await page.goto('/settings?tab=appearance')
  return page.getByRole('radiogroup', { name: 'Box art size' })
}
async function measure(page: Page, screen: string, size: string) {
  await page.goto(screen)
  await expect(page.locator('html')).toHaveAttribute('data-poster-size', size.toLowerCase())
  const tile = page.locator('.tile-poster').first()
  await expect(tile).toBeVisible()
  return tile.evaluate((el) => {
    const { width, height } = getComputedStyle(el)
    return { width: parseFloat(width), ratio: parseFloat(width) / parseFloat(height) }
  })
}

test('poster size changes shelf and grid density and follows the profile to another device', async ({ page, browser }) => {
  const documents: Record<string, Document> = {}
  await backend(page, documents)
  await signIn(page)
  for (const width of [390, 1280]) {
    await page.setViewportSize({ width, height: 844 })
    const sizes = []
    for (const size of ['Small', 'Standard', 'Large']) {
      const options = await appearance(page)
      await options.getByRole('radio', { name: size }).click()
      await expect.poll(() => documents.p2?.['posterSize.v1']).toBe(size.toLowerCase())
      const shelf = await measure(page, '/', size)
      const grid = await measure(page, '/browse/movies', size)
      expect(shelf.ratio).toBeCloseTo(2/3, 1)
      expect(grid.ratio).toBeCloseTo(2/3, 1)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      sizes.push([shelf.width, grid.width])
    }
    expect(sizes[2][0]).toBeGreaterThan(sizes[1][0])
    expect(sizes[1][0]).toBeGreaterThan(sizes[0][0])
    expect(sizes[2][1]).toBeGreaterThan(sizes[0][1])
  }
  const other = await browser.newContext()
  const device = await other.newPage()
  await backend(device, documents)
  await signIn(device)
  await expect((await appearance(device)).getByRole('radio', { name: 'Large' })).toHaveAttribute('aria-checked', 'true')
  await other.close()
})

test('caption sample plays timed captions and saves the selected text and background', async ({ page }) => {
  const documents: Record<string, Document> = {}
  await backend(page, documents)
  await signIn(page)
  await appearance(page)
  const video = page.getByLabel('Caption preview')
  await video.scrollIntoViewIfNeeded()
  await expect.poll(() => video.evaluate((el: HTMLVideoElement) => el.currentTime > 0 && !!el.textTracks[0]?.activeCues?.length)).toBe(true)
  for (const [field, name] of [['Caption color', 'Yellow'], ['Caption background', 'Solid'], ['Caption size', 'Large']]) {
    await page.getByRole('radiogroup', { name: field }).getByRole('radio', { name }).click()
  }
  expect(documents.p2?.['captions.v1']).toBeUndefined()
  await page.getByRole('button', { name: 'Save caption appearance' }).click()
  await expect.poll(() => documents.p2?.['captions.v1']).toEqual({ size: 'large', color: 'yellow', background: 'solid' })
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-caption-color', 'yellow')
})

test('home rows import from browser once and reset for this profile', async ({ page }) => {
  const documents: Record<string, Document> = {}
  await backend(page, documents)
  await page.addInitScript(() => localStorage.setItem('sh.homePrefs.v1:p2', JSON.stringify({ order: ['live_now'], hidden: ['old-shelf'] })))
  await signIn(page)
  await expect.poll(() => documents.p2?.['homeRows.v1']).toEqual({ order: ['live_now'], hidden: ['old-shelf'] })
  await page.goto('/settings?tab=rows')
  await page.getByRole('button', { name: 'Reset to default', exact: true }).click()
  await expect.poll(() => documents.p2['homeRows.v1']).toEqual({ order: [], hidden: [] })
  await page.reload()
  await expect(page.getByTestId('row-pref').first()).toBeVisible()
  expect(documents.p2['homeRows.v1']).toEqual({ order: [], hidden: [] })
})

test('featured rotation responds to motion preference and the viewer can pause it', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await signIn(page)
  await expect(page.getByRole('button', { name: 'Resume featured rotation' })).toBeVisible()
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  const pause = page.getByRole('button', { name: 'Pause featured rotation' })
  const saved = page.waitForResponse((r) => r.request().method() === 'PATCH' && r.url().includes('/prefs'))
  await pause.click()
  expect((await saved).ok()).toBe(true)
  await expect(page.getByRole('button', { name: 'Resume featured rotation' })).toBeVisible()
  await page.goto('/settings')
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Resume featured rotation' })).not.toHaveAttribute('aria-disabled', 'true')
})
