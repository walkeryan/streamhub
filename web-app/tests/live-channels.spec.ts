import { expect, test, type Page } from '@playwright/test'
import { searchLive } from './helpers'

// Live TV's Channels view scrolls the way the guide does: the list fills the window under the shared header,
// scrolls inside itself, loads channels as its end comes near and draws only the rows near the view.
const OWNER = { id: 'p1', name: 'Owner', avatar: null, is_admin: true, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' }
const PHONE = { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true }

/** Start signed in, past the first-run notice. The second page of channels can be held back (see `release`). */
async function enter(page: Page, path = '/live') {
  await page.addInitScript((profile) => {
    sessionStorage.setItem('mock.accepted', JSON.stringify(['p1', 'p2', 'p3']))
    if (!localStorage.getItem('sh.session.v1')) localStorage.setItem('sh.session.v1', JSON.stringify({ access: `mock-access-${profile.id}`, refresh: `mock-refresh-${profile.id}`, profile }))
    const gate = { hold: false, waiting: [] as Array<() => void> }
    ;(window as unknown as { listGate: typeof gate }).listGate = gate
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const request = new Request(input, init), url = new URL(request.url)
      if (gate.hold && url.pathname === '/api/v1/channels' && Number(url.searchParams.get('offset')) > 0) await new Promise<void>((go) => gate.waiting.push(go))
      return original(request)
    }
  }, OWNER)
  await page.goto(path)
}
const hold = (page: Page) => page.evaluate(() => { (window as unknown as { listGate: { hold: boolean } }).listGate.hold = true })
const release = (page: Page) => page.evaluate(() => {
  const gate = (window as unknown as { listGate: { hold: boolean; waiting: Array<() => void> } }).listGate
  gate.hold = false
  gate.waiting.splice(0).forEach((go) => go())
})
/** A line-up the size a broad filter matches, with invented names; then Live TV, reached without a reload. */
async function bigList(page: Page, count = 1000) {
  await enter(page, '/')
  await expect(page.getByTestId('home')).toBeVisible()
  await page.evaluate(async (count) => {
    const path = '/src/mocks/fixtures.ts'
    const { channels } = await import(/* @vite-ignore */ path)
    const base = channels[0]
    channels.splice(0, channels.length, ...Array.from({ length: count }, (_, i) => ({ ...base, id: `ch:wide:${i}`, number: i + 1, ext_id: String(i), name: `Fixture Channel ${i + 1}`, group: 'News' })))
  }, count)
  const live = page.getByRole('link', { name: 'Live TV', exact: true })
  if (await live.count()) await live.click()
  else await page.getByTestId('rail').getByText('Live TV').tap()
  await expect(rows(page).first()).toBeVisible()
}
const list = (page: Page) => page.getByTestId('channel-list')
const rows = (page: Page) => page.getByTestId('channel-row')
const row = (page: Page, n: number) => page.getByRole('option', { name: new RegExp(`^${n} Fixture Channel ${n}(,|$)`) })
const end = (page: Page) => page.getByTestId('channels-end')
const focusedRow = (page: Page) => list(page).locator('[data-focused="true"]').evaluate((el) => Number((el as HTMLElement).dataset.row))
const fits = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
const geometry = (page: Page) => page.evaluate(() => {
  const view = document.querySelector('[data-testid="channel-list"]')!, area = view.getBoundingClientRect()
  const all = Array.from(document.querySelectorAll('[data-testid="channel-row"]'), (el) => el.getBoundingClientRect())
  return {
    top: area.top, bottom: area.bottom,
    visibleRows: all.filter((r) => r.top >= area.top - 1 && r.bottom <= Math.min(area.bottom, innerHeight) + 1).length,
    heights: [...new Set(all.map((r) => Math.round(r.height)))].length,
    pageScrolls: document.documentElement.scrollHeight > innerHeight || scrollY !== 0,
  }
})
/** Where everything in the header is laid out (the control in focus is drawn a little larger: that is not a move): the title, the switch, the icons, the count, and each chip. */
const header = (page: Page) => page.evaluate(() => {
  const head = document.querySelector('[data-testid="live-head"]')!
  const box = (el: Element) => {
    let x = 0, y = 0
    for (let at = el as HTMLElement | null; at; at = at.offsetParent as HTMLElement | null) { x += at.offsetLeft; y += at.offsetTop }
    return [x, y, (el as HTMLElement).offsetWidth, (el as HTMLElement).offsetHeight]
  }
  const row = head.querySelector('[role="group"][aria-label="Channel groups"]')!, note = head.querySelector('[data-testid="live-note"]')!
  return {
    controls: Array.from(head.querySelectorAll('h1, [role="group"][aria-label="Live TV view"] [data-focus-key], .live-tools [data-focus-key], [role="group"][aria-label="Channel groups"] [data-focus-key]'), (el) => [el.textContent || el.getAttribute('aria-label'), ...box(el)]),
    head: box(head), row: box(row), note: box(note),
  }
})

for (const size of [{ width: 1600, height: 830 }, { width: 1280, height: 720 }]) {
  test.describe(`on a ${size.width} by ${size.height} window`, () => {
    test.use({ viewport: size })

    test('the list fills the window and scrolls inside itself, loads as it goes with a bounded number of rows, and the preview stays put', async ({ page }) => {
      const offsets: number[] = []
      page.on('request', (request) => { const url = new URL(request.url()); if (url.pathname === '/api/v1/channels') offsets.push(Number(url.searchParams.get('offset'))) })
      await bigList(page)
      // The count is the filter's, not what is loaded. No pages, and no range of them.
      await expect(page.getByText('1,000 channels', { exact: true })).toBeVisible()
      await expect(page.getByRole('button', { name: /Previous page|Next page/ })).toHaveCount(0)
      await expect(page.getByText(/\d+–\d+ of \d/)).toHaveCount(0)
      await expect(row(page, 1)).toHaveAttribute('data-focused', 'true')
      const seen = await geometry(page)
      expect(seen.pageScrolls).toBe(false)
      expect(seen.bottom).toBeLessThanOrEqual(size.height)
      expect(size.height - seen.bottom).toBeLessThan(16)
      expect(seen.visibleRows).toBeGreaterThanOrEqual(4)
      expect(seen.heights).toBe(2) // every row one height, but the focused one, which is drawn a little larger
      expect(await rows(page).count()).toBeLessThanOrEqual(30)
      expect(offsets).toEqual([0])
      await expect(end(page)).toHaveText('Loading more channels…')

      // The preview, the channel's name and what is on, and the controls: all on screen, beside the list.
      const pane = page.getByRole('complementary', { name: 'Preview', exact: true })
      const controls = ['Watch full screen', 'Preview off', 'Add favorite', 'Record'].map((name) => pane.getByRole('button', { name, exact: true }))
      const place = async () => Promise.all([page.getByTestId('live-preview'), page.getByTestId('live-info'), ...controls].map((part) => part.boundingBox()))
      await expect(page.getByTestId('live-info')).toContainText('1 · Fixture Channel 1')
      await expect(page.getByTestId('live-info')).toContainText(/Now: .+Next: /)
      for (const part of [page.getByTestId('live-preview'), page.getByTestId('live-info'), ...controls]) await expect(part).toBeInViewport({ ratio: 1 })
      const before = await place()
      expect(before[0]!.x).toBeGreaterThan((await list(page).boundingBox())!.x + (await list(page).boundingBox())!.width - 1)

      // Scrolling to the end of what is loaded asks for the next forty; the rows drawn stay few.
      for (const loaded of [40, 80, 120]) {
        await list(page).evaluate((el) => { el.scrollTop = el.scrollHeight })
        await expect(row(page, loaded + 1)).toBeVisible()
        expect(await rows(page).count()).toBeLessThanOrEqual(30)
      }
      expect(offsets).toEqual([0, 40, 80, 120])
      expect(await list(page).evaluate((el) => el.scrollTop)).toBeGreaterThan(5000)
      // The list moved; the page and the preview did not.
      expect((await geometry(page)).pageScrolls).toBe(false)
      expect(await place()).toEqual(before)
      for (const part of controls) await expect(part).toBeInViewport({ ratio: 1 })
      // The focused row is far above the view and still drawn: focus was not recycled with its row.
      await expect(row(page, 1)).toHaveAttribute('data-focused', 'true')
      expect(await focusedRow(page)).toBe(0)

      // Another filter starts again from the first channel, at the top, and says where the list ends.
      await searchLive(page, 'Channel 99')
      await expect(page.getByText('11 channels', { exact: true })).toBeVisible()
      await expect(row(page, 99)).toBeVisible()
      await expect(row(page, 1)).toHaveCount(0)
      expect(await list(page).evaluate((el) => el.scrollTop)).toBe(0)
      await list(page).evaluate((el) => { el.scrollTop = el.scrollHeight })
      await expect(row(page, 999)).toBeVisible()
      await expect(end(page)).toHaveText('End of the list · 11 channels')
      await expect(end(page)).toBeInViewport()
      expect((await geometry(page)).pageScrolls).toBe(false)
    })
  })
}

test.describe('by keyboard, on a 1600 by 830 window', () => {
  test.use({ viewport: { width: 1600, height: 830 } })

  test('Down past the last channel loaded keeps focus and moves on when more arrive; Up from the first is the chips; the list is one Tab stop', async ({ page }) => {
    await bigList(page)
    await expect(row(page, 1)).toHaveAttribute('data-focused', 'true')
    // Up from the first row leaves the list for the header's groups; Down comes back to it.
    await page.keyboard.press('ArrowUp')
    await expect(page.getByRole('group', { name: 'Channel groups', exact: true }).getByRole('button', { name: 'All', exact: true })).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('ArrowDown')
    await expect(row(page, 1)).toHaveAttribute('data-focused', 'true')

    await hold(page)
    for (let i = 0; i < 39; i++) await page.keyboard.press('ArrowDown')
    expect(await focusedRow(page)).toBe(39)
    await expect(row(page, 40)).toBeInViewport({ ratio: 0.99 })
    await expect(end(page)).toHaveText('Loading more channels…')
    // The next page has not arrived: focus waits on the last row instead of being lost.
    await page.keyboard.press('ArrowDown')
    await expect(list(page).locator('[data-focused="true"]')).toHaveCount(1)
    expect(await focusedRow(page)).toBe(39)
    await release(page)
    await expect.poll(() => focusedRow(page)).toBe(40)
    // And on down the list, a page at a time, with a bounded number of rows drawn.
    for (let i = 0; i < 80 && (await focusedRow(page)) < 90; i++) await page.keyboard.press('ArrowDown')
    expect(await focusedRow(page)).toBe(90)
    await expect(row(page, 91)).toBeInViewport({ ratio: 0.99 })
    await expect(page.getByTestId('live-info')).toContainText('91 · Fixture Channel 91')
    expect(await rows(page).count()).toBeLessThanOrEqual(30)
    expect((await geometry(page)).pageScrolls).toBe(false)

    // Tab leaves the list in one press, for the controls beside it; Shift+Tab comes back to the same row.
    expect(await page.locator('[data-tab-group="channels"]').count()).toBeGreaterThan(8)
    await page.keyboard.press('Tab')
    await expect(page.getByRole('button', { name: 'Watch full screen', exact: true })).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('Shift+Tab')
    await expect(row(page, 91)).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('Shift+Tab')
    await expect(list(page).locator('[data-focused="true"]')).toHaveCount(0)
    await expect(page.getByRole('group', { name: 'Channel groups', exact: true }).locator('[data-focused="true"]')).toHaveCount(1)
    // Right is the controls beside the list, Left the row that was left.
    await page.keyboard.press('Tab')
    await page.keyboard.press('ArrowRight')
    await expect(page.getByRole('complementary', { name: 'Preview', exact: true }).locator('[data-focused="true"]')).toHaveCount(1)
    await page.keyboard.press('ArrowLeft')
    await expect(row(page, 91)).toHaveAttribute('data-focused', 'true')
  })

  test('Back from the player, or from another screen, restores the search, the row and where the list was', async ({ page }) => {
    await bigList(page)
    const searched = page.waitForResponse((response) => new URL(response.url()).searchParams.get('q') === 'Fixture')
    await searchLive(page, 'Fixture')
    await searched
    await expect(page.getByText('1,000 channels', { exact: true })).toBeVisible()
    await expect(end(page)).toHaveText('Loading more channels…')
    for (const loaded of [40, 80]) {
      await list(page).evaluate((el) => { el.scrollTop = el.scrollHeight })
      await expect(row(page, loaded + 1)).toBeVisible()
    }
    // On into the third page, by the wheel: only the rows near the view are drawn.
    const height = await list(page).evaluate((el) => { const [a, b] = Array.from(el.querySelectorAll('[data-row]'), (r) => r.getBoundingClientRect().top); return b! - a! })
    await list(page).evaluate((el, height) => { el.scrollTop = 92.5 * height }, height)
    await expect(row(page, 95)).toBeInViewport({ ratio: 0.99 })
    const where = await list(page).evaluate((el) => el.scrollTop)
    await row(page, 95).click()
    await expect(page.getByTestId('player')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('live')).toBeVisible()
    await expect(page.getByLabel('Search channels or number')).toHaveValue('Fixture')
    await expect(row(page, 95)).toHaveAttribute('data-focused', 'true')
    await expect(row(page, 95)).toBeInViewport({ ratio: 0.99 })
    // (to the row: that is how finely the position is kept)
    expect(Math.abs((await list(page).evaluate((el) => el.scrollTop)) - where)).toBeLessThanOrEqual(height + 8)
    expect(await rows(page).count()).toBeLessThanOrEqual(30)
    await expect(page).not.toHaveURL(/page=/)

    await page.getByRole('link', { name: 'Home', exact: true }).click()
    await expect(page.getByTestId('home')).toBeVisible()
    await page.goBack()
    await expect(page.getByRole('button', { name: 'Channels', exact: true })).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByLabel('Search channels or number')).toHaveValue('Fixture')
    await expect(row(page, 95)).toBeInViewport({ ratio: 0.99 })
    await expect(row(page, 95)).toHaveAttribute('data-tab-current', 'true')
    expect(Math.abs((await list(page).evaluate((el) => el.scrollTop)) - where)).toBeLessThanOrEqual(height + 8)
  })
})

for (const size of [{ width: 1600, height: 830 }, { width: 1024, height: 768 }]) {
  test(`the header is the same in both views at ${size.width} px: switching moves nothing in it`, async ({ page }) => {
    await page.setViewportSize(size)
    await enter(page)
    await expect(rows(page).first()).toBeVisible()
    const channels = await header(page)
    expect(channels.controls.map((c) => c[0])).toEqual(['Live TV', 'Guide', 'Channels', 'Search channels', 'Options', 'All', 'Favorites', 'News', 'Sports', 'Movies', 'Kids', 'Music', 'Local'])
    await page.getByRole('button', { name: 'Guide', exact: true }).click()
    await expect(page.locator('.epg-cell[data-now="true"]').first()).toBeVisible()
    const guide = await header(page)
    expect(guide.controls).toEqual(channels.controls)
    expect(guide.head).toEqual(channels.head)
    // The row of chips and the count start where they did. From 1100 px up the guide's details panel is beside
    // the header, so they are narrower there; below that they are the same boxes.
    expect([guide.row[0], guide.row[1], guide.row[3], guide.note[0], guide.note[1], guide.note[3]]).toEqual([channels.row[0], channels.row[1], channels.row[3], channels.note[0], channels.note[1], channels.note[3]])
    if (size.width < 1100) expect([guide.row, guide.note]).toEqual([channels.row, channels.note])
    await page.getByRole('button', { name: 'Channels', exact: true }).click()
    await expect(rows(page).first()).toBeVisible()
    expect(await header(page)).toEqual(channels)
  })
}

test.describe('on a phone', () => {
  test.use(PHONE)

  test('the list fills the height between the header and the bottom bar and scrolls inside itself; the header is the guide\'s', async ({ page }) => {
    await bigList(page)
    const seen = await geometry(page), bar = (await page.getByTestId('rail').boundingBox())!
    expect(seen.bottom).toBeLessThanOrEqual(bar.y + 1)
    expect(bar.y - seen.bottom).toBeLessThan(16)
    expect(seen.pageScrolls).toBe(false)
    expect(seen.visibleRows).toBeGreaterThanOrEqual(6)
    expect(await fits(page)).toBe(true)
    await expect(page.getByTestId('live-preview')).toHaveCount(0)
    await expect(page.getByRole('button', { name: /Previous page|Next page/ })).toHaveCount(0)
    // It scrolls inside itself and loads as it goes; the page stays where it is.
    await list(page).evaluate((el) => { el.scrollTop = el.scrollHeight })
    await expect(row(page, 41)).toBeVisible()
    await list(page).evaluate((el) => { el.scrollTop = el.scrollHeight })
    await expect(row(page, 81)).toBeVisible()
    expect(await rows(page).count()).toBeLessThanOrEqual(30)
    expect((await geometry(page)).pageScrolls).toBe(false)
    expect(await fits(page)).toBe(true)

    const channels = await header(page)
    await page.getByRole('button', { name: 'Guide', exact: true }).tap()
    await expect(page.locator('.epg-cell[data-now="true"]').first()).toBeVisible()
    expect(await header(page)).toEqual(channels)
    await page.getByRole('button', { name: 'Channels', exact: true }).tap()

    // A tap on a channel plays it.
    await rows(page).nth(3).tap()
    await expect(page).toHaveURL(/\/play\/ch/)
  })
})
