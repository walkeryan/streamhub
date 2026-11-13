import { expect, test, type Page } from '@playwright/test'
import { chooseLiveOption, liveChoice, searchLive } from './helpers'

// The guide under Live TV's compact header: a grid that fills the window, scrolls inside itself and loads
// channels and hours as it goes, with the focused programme described beside the header.
const OWNER = { id: 'p1', name: 'Owner', avatar: null, is_admin: true, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' }
const PHONE = { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true }

/** Start signed in, past the first-run notice. The second page of channels can be held back (see `release`). */
async function enter(page: Page, path = '/live?view=guide') {
  await page.addInitScript((profile) => {
    sessionStorage.setItem('mock.accepted', JSON.stringify(['p1', 'p2', 'p3']))
    if (!localStorage.getItem('sh.session.v1')) localStorage.setItem('sh.session.v1', JSON.stringify({ access: `mock-access-${profile.id}`, refresh: `mock-refresh-${profile.id}`, profile }))
    const gate = { hold: false, waiting: [] as Array<() => void> }
    ;(window as unknown as { guideGate: typeof gate }).guideGate = gate
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const request = new Request(input, init), url = new URL(request.url)
      if (gate.hold && url.pathname === '/api/v1/channels' && Number(url.searchParams.get('offset')) > 0) await new Promise<void>((go) => gate.waiting.push(go))
      return original(request)
    }
  }, OWNER)
  await page.goto(path)
}
const hold = (page: Page) => page.evaluate(() => { (window as unknown as { guideGate: { hold: boolean } }).guideGate.hold = true })
const release = (page: Page) => page.evaluate(() => {
  const gate = (window as unknown as { guideGate: { hold: boolean; waiting: Array<() => void> } }).guideGate
  gate.hold = false
  gate.waiting.splice(0).forEach((go) => go())
})
/** A line-up the size a broad filter matches, with invented names; then the guide, reached without a reload. */
async function bigGuide(page: Page, count = 1000) {
  await enter(page, '/')
  await expect(page.getByTestId('home')).toBeVisible()
  await page.evaluate(async (count) => {
    const path = '/src/mocks/fixtures.ts'
    const { channels } = await import(/* @vite-ignore */ path)
    const base = channels[0]
    channels.splice(0, channels.length, ...Array.from({ length: count }, (_, i) => ({ ...base, id: `ch:wide:${i}`, number: i + 1, ext_id: String(i), name: `Fixture Channel ${i + 1}`, group: 'News' })))
  }, count)
  await page.getByRole('link', { name: 'Live TV', exact: true }).click()
  await page.getByRole('button', { name: 'Guide', exact: true }).click()
  await expect(nowCells(page).first()).toBeVisible()
}
const grid = (page: Page) => page.getByTestId('guide-grid')
const rows = (page: Page) => page.getByTestId('guide-row')
const nowCells = (page: Page) => page.locator('.epg-cell[data-now="true"]')
const focusedCell = (page: Page) => grid(page).locator('.fx[data-focused="true"]')
const info = (page: Page) => page.getByTestId('guide-info')
const fits = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
/** The row a focused cell is in. */
const focusedRow = (page: Page) => focusedCell(page).evaluate((el) => Number((el.closest('[data-row]') as HTMLElement).dataset.row))
const geometry = (page: Page) => page.evaluate(() => {
  const box = (testId: string) => document.querySelector(`[data-testid="${testId}"]`)!.getBoundingClientRect()
  const area = box('guide-grid'), head = document.querySelector('.epg-head')!.getBoundingClientRect()
  const view = document.querySelector('[data-testid="guide-grid"]')!
  const all = Array.from(document.querySelectorAll('[data-testid="guide-row"]'), (el) => el.getBoundingClientRect())
  return {
    header: area.top - (innerWidth > 720 ? box('rail').bottom : 0), top: area.top, bottom: area.bottom,
    visibleRows: all.filter((r) => r.top >= head.bottom - 1 && r.bottom <= Math.min(area.top + view.clientHeight, innerHeight) + 1).length,
    pageScrolls: document.documentElement.scrollHeight > innerHeight,
  }
})

test.describe('on a 1600 by 830 window', () => {
  test.use({ viewport: { width: 1600, height: 830 } })

  test('the header takes at most 150 px, nine channels or more are in view, and the page itself does not scroll', async ({ page }) => {
    await enter(page)
    await expect(nowCells(page).first()).toBeVisible()
    const seen = await geometry(page)
    expect(seen.header).toBeLessThanOrEqual(150)
    expect(seen.visibleRows).toBeGreaterThanOrEqual(9)
    expect(seen.pageScrolls).toBe(false)
    expect(seen.bottom).toBeLessThanOrEqual(830)
    // Nothing of the old rows of controls: no pages, no schedules chips, no wide search field, no Clear filters.
    await expect(page.getByRole('button', { name: /Next channels|Previous channels|With schedules|All channels|Clear filters/ })).toHaveCount(0)
    await expect(page.getByText(/Page \d+ of/)).toHaveCount(0)
    await expect(page.getByLabel('Search channels or number')).toHaveCount(0)
    await expect(page.getByText('22 channels', { exact: true })).toBeVisible()
    // The programme details are beside the header, not under the grid.
    const panel = (await info(page).boundingBox())!
    expect(panel.x).toBeGreaterThan(700)
    expect(panel.y + panel.height).toBeLessThanOrEqual(seen.top)
    // Channels shares the header: switching views moves nothing in it.
    const place = async (name: string) => (await page.getByRole('button', { name, exact: true }).boundingBox())!
    const before = [await place('Guide'), await place('Options'), await place('Sports')]
    await page.getByRole('button', { name: 'Channels', exact: true }).click()
    await expect(page.getByTestId('channel-row').first()).toBeVisible()
    expect([await place('Guide'), await place('Options'), await place('Sports')]).toEqual(before)
  })

  test('focusing a programme fills the panel beside the header and never moves the grid', async ({ page }) => {
    await enter(page)
    await expect(nowCells(page).first()).toHaveAttribute('data-focused', 'true')
    const top = (await geometry(page)).top
    const title = async () => (await focusedCell(page).getAttribute('aria-label'))!.replace(/^[^:]+: /, '').replace(/, \d.*$/, '')
    for (const key of ['ArrowRight', 'ArrowDown', 'ArrowDown', 'ArrowRight', 'ArrowUp', 'ArrowLeft']) {
      await page.keyboard.press(key)
      await expect(info(page).getByRole('heading')).toHaveText(await title())
      expect((await geometry(page)).top).toBe(top)
    }
    await expect(info(page)).toContainText(/\d+:\d\d [AP]M to \d+:\d\d [AP]M · /)
    await expect(info(page)).toContainText('Fixture programme.')
    // A channel alone: what it has on now, and the same actions.
    await page.getByTestId('guide').getByRole('button', { name: '3 Cinema Classics', exact: true }).hover()
    await expect(info(page)).toContainText('3 Cinema Classics')
    await expect(info(page).getByRole('heading')).toHaveText(await nowCells(page).nth(2).locator('.truncate').first().innerText())
    expect((await geometry(page)).top).toBe(top)
    await info(page).getByRole('button', { name: 'Watch', exact: true }).click()
    await expect(page).toHaveURL(/\/play\/ch/)
  })

  test('arrows keep to the hour between rows, Up from the first row is the header, and Back restores where the guide was', async ({ page }) => {
    await enter(page)
    await expect(nowCells(page).first()).toHaveAttribute('data-focused', 'true')
    // Down from what is on now is what is on now on the next channel, however long either runs.
    await page.keyboard.press('ArrowDown')
    await expect(focusedCell(page)).toHaveAttribute('data-now', 'true')
    expect(await focusedRow(page)).toBe(1)
    // Right is the next programme on this channel; Down then lands on what is on when it starts.
    await page.keyboard.press('ArrowRight')
    const start = (await focusedCell(page).boundingBox())!.x
    await page.keyboard.press('ArrowDown')
    const under = (await focusedCell(page).boundingBox())!
    expect(await focusedRow(page)).toBe(2)
    expect(under.x).toBeLessThanOrEqual(start + 1)
    expect(under.x + under.width).toBeGreaterThan(start + 1)
    for (let i = 0; i < 6; i++) await page.keyboard.press('ArrowDown')
    await page.keyboard.press('ArrowRight')
    await page.keyboard.press('ArrowRight')
    expect(await focusedRow(page)).toBe(8)
    const chosen = (await focusedCell(page).getAttribute('aria-label'))!
    const where = await grid(page).evaluate((el) => [el.scrollTop, el.scrollLeft])
    expect(where[1]).toBeGreaterThan(0)

    // Away and Back: the view, the hour, the row and the programme are as they were.
    await page.getByRole('link', { name: 'Home', exact: true }).click()
    await expect(page.getByTestId('home')).toBeVisible()
    await page.goBack()
    await expect(page.getByRole('button', { name: 'Guide', exact: true })).toHaveAttribute('aria-pressed', 'true')
    await expect(info(page).getByRole('heading')).toHaveText(chosen.replace(/^[^:]+: /, '').replace(/, \d.*$/, ''))
    // (to the row and to the half hour: that is how finely the position is kept)
    const [row, slot] = await grid(page).evaluate((el) => [el.querySelector('.epg-row')!.getBoundingClientRect().height, el.querySelector('.epg-tick')!.getBoundingClientRect().width])
    await expect.poll(async () => Math.abs((await grid(page).evaluate((el) => el.scrollLeft)) - where[1]!)).toBeLessThanOrEqual(slot!)
    expect(Math.abs((await grid(page).evaluate((el) => el.scrollTop)) - where[0]!)).toBeLessThanOrEqual(row!)
    await expect(page.getByRole('button', { name: chosen, exact: true })).toBeInViewport()
    await expect(page.getByRole('button', { name: chosen, exact: true })).toHaveAttribute('data-tab-current', 'true')

    // Up from the first row leaves the grid for the header's groups; Down comes back to the same cell.
    await page.getByRole('button', { name: chosen, exact: true }).hover()
    for (let i = 0; i < 8; i++) await page.keyboard.press('ArrowUp')
    expect(await focusedRow(page)).toBe(0)
    const first = (await focusedCell(page).getAttribute('aria-label'))!
    await page.keyboard.press('ArrowUp')
    await expect(page.getByRole('group', { name: 'Channel groups', exact: true }).locator('[data-focused="true"]')).toHaveCount(1)
    await page.keyboard.press('ArrowDown')
    await expect(page.getByRole('button', { name: first, exact: true })).toHaveAttribute('data-focused', 'true')
  })

  test('scrolling to the bottom loads the next channels and the rows drawn stay bounded over 1,000 channels', async ({ page }) => {
    const offsets: number[] = []
    page.on('request', (request) => { const url = new URL(request.url()); if (url.pathname === '/api/v1/channels' && url.searchParams.get('has_epg')) offsets.push(Number(url.searchParams.get('offset'))) })
    await bigGuide(page)
    await expect(page.getByText('1,000 channels', { exact: true })).toBeVisible()
    expect(await rows(page).count()).toBeLessThanOrEqual(30)
    expect(offsets).toEqual([0])
    await expect(page.getByTestId('guide-end')).toHaveText('Loading more channels…')
    for (const page2 of [40, 80, 120]) {
      await grid(page).evaluate((el) => { el.scrollTop = el.scrollHeight })
      await expect(page.getByTestId('guide').getByRole('button', { name: `${page2 + 1} Fixture Channel ${page2 + 1}`, exact: true })).toBeVisible()
      expect(await rows(page).count()).toBeLessThanOrEqual(30)
      expect(await page.locator('.epg-cell').count()).toBeLessThanOrEqual(600)
    }
    expect(offsets).toEqual([0, 40, 80, 120])
    // The focused cell is far above the view and still drawn: focus was not recycled with its row.
    await expect(focusedCell(page)).toHaveCount(1)
    expect(await focusedRow(page)).toBe(0)
    // Another filter starts again from the first channel, at the top; the hour on screen stays.
    await grid(page).evaluate((el) => { el.scrollLeft += 2 * el.querySelector('.epg-tick')!.getBoundingClientRect().width })
    const hour = await grid(page).evaluate((el) => el.scrollLeft)
    await searchLive(page, 'Channel 99')
    await expect(page.getByText('11 channels', { exact: true })).toBeVisible()
    await expect(page.getByTestId('guide').getByRole('button', { name: '99 Fixture Channel 99', exact: true })).toBeVisible()
    expect(await grid(page).evaluate((el) => [el.scrollTop, el.scrollLeft])).toEqual([0, hour])
    await expect(page.getByTestId('guide-end')).toHaveText('End of the list · 11 channels')
  })

  test('keyboard only: Down past the last channel loaded loads more and keeps focus', async ({ page }) => {
    await bigGuide(page)
    await expect(nowCells(page).first()).toHaveAttribute('data-focused', 'true')
    await hold(page)
    for (let i = 0; i < 39; i++) await page.keyboard.press('ArrowDown')
    expect(await focusedRow(page)).toBe(39)
    await expect(page.getByTestId('guide-end')).toHaveText('Loading more channels…')
    // The next page has not arrived: focus waits on the last row instead of being lost.
    await page.keyboard.press('ArrowDown')
    await expect(focusedCell(page)).toHaveCount(1)
    expect(await focusedRow(page)).toBe(39)
    await release(page)
    await expect.poll(() => focusedRow(page)).toBe(40)
    await expect(focusedCell(page)).toHaveAttribute('data-now', 'true')
    // And on down the list, a page at a time, with a bounded number of rows drawn.
    for (let i = 0; i < 80 && (await focusedRow(page)) < 90; i++) await page.keyboard.press('ArrowDown')
    expect(await focusedRow(page)).toBe(90)
    await expect(focusedCell(page)).toBeInViewport()
    expect(await rows(page).count()).toBeLessThanOrEqual(30)
  })

  test('scrolling right loads later hours a few at a time, up to a day ahead; Now comes back', async ({ page }) => {
    const spans: string[] = []
    page.on('request', (request) => { const url = new URL(request.url()); if (url.pathname === '/api/v1/epg') spans.push(`${url.searchParams.get('from')} ${url.searchParams.get('to')}`) })
    await enter(page)
    await expect(nowCells(page).first()).toBeVisible()
    const ticks = page.locator('.epg-tick')
    await expect(ticks).toHaveCount(24) // twelve hours: three back, nine ahead
    expect(new Set(spans).size).toBe(1)
    await grid(page).evaluate((el) => { el.scrollLeft = el.scrollWidth })
    await expect(ticks).toHaveCount(32)
    // Only the new four hours are asked for, starting where the first request ended.
    await expect.poll(() => new Set(spans).size).toBe(2)
    const [first, second] = [...new Set(spans)].map((s) => s.split(' ').map((t) => Date.parse(t)))
    expect(second![0]).toBe(first![1])
    expect(second![1]! - second![0]!).toBe(4 * 3600_000)
    const edge = (await ticks.nth(24).boundingBox())!.x
    await expect.poll(() => page.locator('.epg-cell').evaluateAll((cells, x) => cells.some((c) => c.getBoundingClientRect().left > x), edge)).toBe(true)
    // On to the horizon: 27 hours in all, and no further.
    for (let i = 0; i < 6; i++) { await grid(page).evaluate((el) => { el.scrollLeft = el.scrollWidth }); await page.waitForTimeout(150) }
    await expect(ticks).toHaveCount(54)
    await expect(page.getByTestId('now-line')).not.toBeInViewport()
    await page.getByRole('button', { name: 'Now', exact: true }).click()
    await expect(page.getByTestId('now-line')).toBeInViewport()
    await expect(nowCells(page).first()).toBeInViewport()
  })

  test('Tab enters the grid at the current cell and the next Tab leaves it', async ({ page }) => {
    await enter(page)
    await expect(nowCells(page).first()).toHaveAttribute('data-focused', 'true')
    expect(await page.locator('[data-tab-group="guide"]').count()).toBeGreaterThan(100)
    const current = (await focusedCell(page).getAttribute('aria-label'))!
    // Back out of the grid: the corner's Now is the stop before its cells.
    await page.keyboard.press('Shift+Tab')
    await expect(page.getByRole('button', { name: 'Now', exact: true })).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('Tab')
    await expect(page.getByRole('button', { name: current, exact: true })).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('Tab')
    await expect(grid(page).locator('[data-focused="true"]')).toHaveCount(0)
    await expect(page.locator('.fx[data-focused="true"]')).toHaveCount(1)
    // And in again from the far side, on the same cell.
    await page.keyboard.press('Shift+Tab')
    await expect(page.getByRole('button', { name: current, exact: true })).toHaveAttribute('data-focused', 'true')
  })

  test('the schedules choice is in Options, and the header names it only when it is not the usual one', async ({ page }) => {
    await enter(page)
    await expect(nowCells(page).first()).toBeVisible()
    await expect(page.getByTestId('live-choice')).toHaveCount(0)
    await chooseLiveOption(page, 'All channels')
    await expect(liveChoice(page, 'All channels')).toBeVisible()
    await expect(nowCells(page).first()).toBeVisible()
    // It is the guide's choice: Channels does not show it.
    await page.getByRole('button', { name: 'Channels', exact: true }).click()
    await expect(page.getByTestId('live-choice')).toHaveCount(0)
    await page.getByRole('button', { name: 'Guide', exact: true }).click()
    await chooseLiveOption(page, 'With schedules')
    await expect(page.getByTestId('live-choice')).toHaveCount(0)
    // Search is an icon that opens a field, stays while it holds text, and clears with one small ×.
    await searchLive(page, 'Metro')
    await expect(page.getByText('1 channels', { exact: true })).toBeVisible()
    await page.getByTestId('guide').getByRole('button', { name: '2 Metro Sports 1', exact: true }).hover()
    await expect(page.getByLabel('Search channels or number')).toHaveValue('Metro')
    const clear = page.getByRole('button', { name: 'Clear filters', exact: true })
    expect((await clear.boundingBox())!.width).toBeLessThan(60)
    await clear.click()
    await expect(page.getByText('22 channels', { exact: true })).toBeVisible()
    await expect(page.getByLabel('Search channels or number')).toHaveCount(0)
    await expect(clear).toHaveCount(0)
  })
})

test('below 1100 px the details are one line over the grid that opens the rest', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 })
  await enter(page)
  await expect(nowCells(page).first()).toHaveAttribute('data-focused', 'true')
  const line = info(page), top = (await geometry(page)).top
  expect((await line.boundingBox())!.height).toBeLessThanOrEqual(32)
  expect((await line.boundingBox())!.y + (await line.boundingBox())!.height).toBeLessThanOrEqual(top)
  const title = (await focusedCell(page).getAttribute('aria-label'))!.replace(/^[^:]+: /, '').replace(/, \d.*$/, '')
  await expect(line).toContainText(title)
  await page.keyboard.press('ArrowDown')
  expect((await geometry(page)).top).toBe(top)
  // Up from the first row is the line; OK opens the details in a sheet.
  await page.keyboard.press('ArrowUp')
  await page.keyboard.press('ArrowUp')
  await expect(line).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  const sheet = page.getByTestId('guide-sheet')
  await expect(sheet.getByRole('heading')).toHaveText(title)
  await expect(sheet).toContainText('Fixture programme.')
  await expect(sheet.getByRole('button', { name: 'Watch', exact: true })).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Escape')
  await expect(sheet).toHaveCount(0)
  await expect(line).toHaveAttribute('data-focused', 'true')
})

test.describe('on a phone', () => {
  test.use(PHONE)

  test('title, switch and icons on one line, chips on the next, a details line, and a grid that fills the rest without sideways page scroll', async ({ page }) => {
    await enter(page)
    await expect(nowCells(page).first()).toBeVisible()
    expect(await fits(page)).toBe(true)
    const middle = async (name: string) => { const box = (await page.getByRole('button', { name, exact: true }).boundingBox())!; return Math.round(box.y + box.height / 2) }
    const heading = (await page.getByRole('heading', { name: 'Live TV', exact: true }).boundingBox())!
    const line = await middle('Guide')
    expect(Math.abs(heading.y + heading.height / 2 - line)).toBeLessThan(3)
    for (const name of ['Channels', 'Search channels', 'Options']) expect(Math.abs((await middle(name)) - line)).toBeLessThan(3)
    const chips = page.getByRole('group', { name: 'Channel groups', exact: true })
    expect((await chips.boundingBox())!.y).toBeGreaterThan(line)
    expect(await chips.evaluate((row) => row.scrollWidth > row.clientWidth)).toBe(true)
    // The grid ends at the bottom bar and the page does not scroll, down or sideways.
    const seen = await geometry(page), bar = (await page.getByTestId('rail').boundingBox())!
    expect(seen.header).toBeLessThanOrEqual(150)
    expect(seen.bottom).toBeLessThanOrEqual(bar.y + 1)
    expect(bar.y - seen.bottom).toBeLessThan(16)
    expect(seen.pageScrolls).toBe(false)
    expect(seen.visibleRows).toBeGreaterThanOrEqual(9)
    // The grid scrolls inside itself, both ways.
    await grid(page).evaluate((el) => { el.scrollLeft = 600; el.scrollTop = 300 })
    expect(await grid(page).evaluate((el) => [el.scrollLeft, el.scrollTop])).toEqual([600, 300])
    expect(await fits(page)).toBe(true)

    // A tap on a channel plays it.
    await page.getByTestId('guide').getByRole('button', { name: /^8 / }).tap()
    await expect(page).toHaveURL(/\/play\/ch/)
  })

  test('the details line opens a sheet, search opens under the first line, Options is a sheet', async ({ page }) => {
    await enter(page)
    await expect(nowCells(page).first()).toBeVisible()
    // This profile may record: a tap on a later programme selects it and offers to record it.
    const later = rows(page).first().locator('.epg-cell[data-now="false"][data-past="false"]').first()
    await later.tap()
    await page.getByTestId('record-dialog').getByRole('button', { name: 'Close', exact: true }).tap()
    await expect(page.getByTestId('record-dialog')).toHaveCount(0)
    const line = info(page)
    await expect(line).toContainText((await later.locator('.truncate').first().innerText()).trim())
    await line.tap()
    const sheet = page.getByTestId('guide-sheet')
    await expect(sheet).toContainText('Fixture programme.')
    const box = (await sheet.boundingBox())!
    expect([box.x, box.width, Math.round(box.y + box.height)]).toEqual([0, 390, 844])
    expect(await fits(page)).toBe(true)
    await sheet.getByRole('button', { name: 'Close', exact: true }).tap()
    await expect(sheet).toHaveCount(0)

    await page.getByRole('button', { name: 'Search channels', exact: true }).tap()
    await expect(page.getByLabel('Search channels or number')).toBeFocused()
    await page.getByLabel('Search channels or number').fill('Metro')
    await expect(page.getByTestId('guide').getByRole('button', { name: '2 Metro Sports 1', exact: true })).toBeVisible()
    expect(await fits(page)).toBe(true)
    await page.getByRole('button', { name: 'Clear filters', exact: true }).tap()
    await expect(page.getByLabel('Search channels or number')).toHaveCount(0)

    await page.getByRole('button', { name: 'Options', exact: true }).tap()
    const options = page.getByTestId('live-options-sheet'), at = (await options.boundingBox())!
    expect([at.x, at.width, Math.round(at.y + at.height)]).toEqual([0, 390, 844])
    await expect(options.getByRole('button', { name: 'With schedules', exact: true })).toHaveAttribute('aria-pressed', 'true')
    expect(await fits(page)).toBe(true)
  })
})
