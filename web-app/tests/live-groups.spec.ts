import { expect, test, type Locator, type Page } from '@playwright/test'

// Channel groups have two levels. The fixture's Sports has a channel of its own and three children;
// the other groups are plain. A guest sees four of its channels: one in each child, one in Sports itself.
// A group with children is one chip that drops them down: no second control beside it, and no × in the row.
const PROFILES = {
  guest: { id: 'p2', name: 'Guest', avatar: null, is_admin: false, theme_id: 'midnight', languages: ['eng', 'spa'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' },
  owner: { id: 'p1', name: 'Owner', avatar: null, is_admin: true, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' },
}
const PHONE = { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true }
const TOP_LEVEL = ['All', 'Favorites', 'News', 'Sports', 'Movies', 'Kids', 'Music', 'Local']
const SPORTS = [/Metro Sports 1/, /Court Vision/, /Sunday League/, /Old Time TV/]

/** Start signed in, past the first-run notice. */
async function enter(page: Page, path = '/live', who: keyof typeof PROFILES = 'guest') {
  await page.addInitScript((profile) => {
    sessionStorage.setItem('mock.accepted', JSON.stringify(['p1', 'p2', 'p3']))
    if (!localStorage.getItem('sh.session.v1')) localStorage.setItem('sh.session.v1', JSON.stringify({ access: `mock-access-${profile.id}`, refresh: `mock-refresh-${profile.id}`, profile }))
  }, PROFILES[who])
  await page.goto(path)
}
const groups = (page: Page) => page.getByRole('group', { name: 'Channel groups', exact: true })
const chip = (page: Page, name: string) => groups(page).getByRole('button', { name, exact: true })
/** The Sports chip, whatever it names: "Sports", or "Sports · Football" while a child is the choice. */
const sports = (page: Page) => groups(page).getByRole('button', { name: /^Sports/ })
const rows = (page: Page) => page.getByTestId('channel-row')
const menu = (page: Page, name = 'Sports') => page.getByRole('menu', { name, exact: true })
const entries = (page: Page) => page.getByRole('menuitemradio')
const entry = (page: Page, name: string) => page.getByRole('menuitemradio', { name: new RegExp(`^${name} \\(`) })
const names = (target: Locator) => target.evaluateAll((all) => all.map((el) => el.getAttribute('aria-label')))
/** Everything in the row of chips, in order, by its text: a control that is not a chip would show here. */
const inRow = (page: Page) => groups(page).evaluate((row) => Array.from(row.children, (el) => el.textContent))
const fits = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
const SPORTS_MENU = ['All Sports (4)', 'Basketball (1)', 'Football (1)', 'Hockey (1)']
async function walk(page: Page, key: string, target: Locator, limit = 16) {
  for (let i = 0; i < limit && (await target.getAttribute('data-focused')) !== 'true'; i++) await page.keyboard.press(key)
  await expect(target).toHaveAttribute('data-focused', 'true')
}
/** Another line-up, before Live TV asks for it: enough plain groups to overflow the row, then a parent at its end. */
async function longRow(page: Page) {
  await enter(page, '/')
  await expect(page.getByTestId('home')).toBeVisible()
  await page.evaluate(async () => {
    const path = '/src/mocks/fixtures.ts'
    const { channels } = await import(/* @vite-ignore */ path)
    channels.forEach((c: { group: string }, i: number) => {
      c.group = i < 15 ? `Fixture Group ${i + 1}` : i === 15 ? 'Regional' : i === 16 ? 'Regional / North Coast' : 'Regional / The Longest Running Regional Network Of Them All'
    })
  })
  await page.getByRole('link', { name: 'Live TV', exact: true }).click()
  await expect(rows(page).first()).toBeVisible()
}

test('chips are the top level; a parent is one chip whose menu opens under it and lists All and its children', async ({ page }) => {
  await enter(page)
  await expect(rows(page).first()).toBeVisible()
  await expect(groups(page).getByRole('button')).toHaveText(TOP_LEVEL)
  // Only the group that has children says so.
  await expect(sports(page).locator('svg')).toHaveCount(1)
  await expect(groups(page).locator('.chip svg')).toHaveCount(1)
  const everything = await rows(page).count()

  // Opening chooses nothing: the list under the chip, the filter as it was.
  await sports(page).click()
  await expect(menu(page)).toBeVisible()
  expect(await names(entries(page))).toEqual(SPORTS_MENU)
  await expect(entries(page).locator('.label')).toHaveText(['4', '1', '1', '1'])
  await expect(page.locator('[aria-checked="true"]')).toHaveCount(0)
  await expect(chip(page, 'All')).toHaveAttribute('aria-pressed', 'true')
  await expect(sports(page)).toHaveAttribute('aria-pressed', 'false')
  await expect(rows(page)).toHaveCount(everything)
  const at = (await sports(page).boundingBox())!, list = (await page.locator('.chip-menu').boundingBox())!
  expect(list.y - (at.y + at.height)).toBeGreaterThanOrEqual(0)
  expect(list.y - (at.y + at.height)).toBeLessThan(12)
  expect(Math.abs(list.x - at.x)).toBeLessThan(6)
  expect(list.width).toBeLessThan(400)
  expect(await page.locator('.chip-menu').evaluate((el) => getComputedStyle(el).position)).toBe('fixed')

  // All Sports: the chip is the choice and still says Sports. Nothing else joined the row, and there is no ×.
  await entry(page, 'All Sports').click()
  await expect(menu(page)).toHaveCount(0)
  await expect(rows(page)).toHaveText(SPORTS)
  await expect(sports(page)).toHaveAttribute('aria-pressed', 'true')
  await expect(sports(page)).toHaveAttribute('data-focused', 'true')
  await expect(sports(page)).toHaveAccessibleName('Sports')
  expect(await inRow(page)).toEqual(TOP_LEVEL)
  await expect(groups(page).getByRole('button')).toHaveCount(TOP_LEVEL.length)
  await expect(page.getByRole('button', { name: /clear/i })).toHaveCount(0)
  expect(await groups(page).evaluate((row) => new Set(Array.from(row.children, (el) => (el as HTMLElement).offsetTop + (el as HTMLElement).offsetHeight / 2)).size)).toBe(1)

  // A child: the same chip names it, and the menu marks it.
  await sports(page).click()
  await expect(entry(page, 'All Sports')).toHaveAttribute('aria-checked', 'true')
  await entry(page, 'Football').click()
  await expect(menu(page)).toHaveCount(0)
  await expect(rows(page)).toHaveText([/Sunday League/])
  await expect(sports(page)).toHaveAttribute('aria-pressed', 'true')
  await expect(sports(page)).toHaveAccessibleName('Sports · Football')
  expect(await inRow(page)).toEqual([...TOP_LEVEL.slice(0, 3), 'Sports · Football', ...TOP_LEVEL.slice(4)])
  await expect(groups(page).getByRole('button')).toHaveCount(TOP_LEVEL.length)
  await expect(page.getByRole('button', { name: /clear/i })).toHaveCount(0)
  await expect(page.getByText('1 channels', { exact: true })).toBeVisible()
  await sports(page).click()
  await expect(entry(page, 'Football')).toHaveAttribute('aria-checked', 'true')
  await expect(page.locator('[aria-checked="true"]')).toHaveCount(1)

  // A click anywhere else closes the list and changes nothing.
  await page.mouse.click(900, 600)
  await expect(menu(page)).toHaveCount(0)
  await expect(sports(page)).toHaveAccessibleName('Sports · Football')
  await expect(rows(page)).toHaveText([/Sunday League/])

  // A plain group is chosen at once; Sports is Sports again. All takes the filter off.
  await chip(page, 'News').click()
  await expect(menu(page)).toHaveCount(0)
  await expect(sports(page)).toHaveAttribute('aria-pressed', 'false')
  expect(await inRow(page)).toEqual(TOP_LEVEL)
  await chip(page, 'All').click()
  await expect(rows(page)).toHaveCount(everything)
  await expect(chip(page, 'News')).toHaveAttribute('aria-pressed', 'false')
  expect(await fits(page)).toBe(true)
})

test('the group and the child survive a reload and Back, in the guide too, until All is chosen', async ({ page }) => {
  await enter(page)
  await sports(page).click()
  await entry(page, 'Hockey').click()
  await expect(rows(page)).toHaveText([/Old Time TV/])

  await page.reload()
  await expect(sports(page)).toHaveAttribute('aria-pressed', 'true')
  await expect(sports(page)).toHaveText('Sports · Hockey')
  await expect(rows(page)).toHaveText([/Old Time TV/])
  await expect(chip(page, 'All')).toHaveAttribute('aria-pressed', 'false')

  // The guide has the same row, and is filtered by the same choice: a child alone, then the parent with its children.
  await page.getByRole('button', { name: 'Guide', exact: true }).click()
  const guide = page.getByTestId('guide')
  await expect(guide.getByRole('button', { name: /^\d+ Old Time TV$/ })).toBeVisible()
  await expect(guide.getByRole('button', { name: /^\d+ Metro Sports 1$/ })).toHaveCount(0)
  expect(await inRow(page)).toEqual([...TOP_LEVEL.slice(0, 3), 'Sports · Hockey', ...TOP_LEVEL.slice(4)])
  await sports(page).click()
  expect(await names(entries(page))).toEqual(SPORTS_MENU)
  await expect(entry(page, 'Hockey')).toHaveAttribute('aria-checked', 'true')
  await entry(page, 'All Sports').click()
  for (const name of ['Metro Sports 1', 'Court Vision', 'Sunday League', 'Old Time TV']) await expect(guide.getByRole('button', { name: new RegExp(`^\\d+ ${name}$`) })).toBeVisible()
  await expect(guide.getByRole('button', { name: /^\d+ Harbor News 24$/ })).toHaveCount(0)
  expect(await inRow(page)).toEqual(TOP_LEVEL)
  await page.getByRole('button', { name: 'Channels', exact: true }).click()
  await sports(page).click()
  await entry(page, 'Basketball').click()
  await expect(rows(page)).toHaveText([/Court Vision/])

  // Away and Back.
  await page.getByRole('link', { name: 'Home', exact: true }).click()
  await expect(page.getByTestId('home')).toBeVisible()
  await page.goBack()
  await expect(sports(page)).toHaveAttribute('aria-pressed', 'true')
  await expect(sports(page)).toHaveText('Sports · Basketball')
  await expect(rows(page)).toHaveText([/Court Vision/])

  await chip(page, 'All').click()
  await expect(chip(page, 'All')).toHaveAttribute('aria-pressed', 'true')
  await expect(sports(page)).toHaveAttribute('aria-pressed', 'false')
  await expect(sports(page)).toHaveText('Sports')
  await expect(rows(page).first()).toContainText('Harbor News 24')
  await page.reload()
  await expect(rows(page).first()).toContainText('Harbor News 24')
  await expect(chip(page, 'All')).toHaveAttribute('aria-pressed', 'true')
  await expect(sports(page)).toHaveText('Sports')
})

test('by keyboard alone: Enter opens the menu, arrows and Tab stay in it, Enter chooses and Escape leaves the filter alone', async ({ page }) => {
  await enter(page)
  await expect(rows(page).first()).toBeVisible()
  await expect(page.locator('.fx[data-focused="true"]')).not.toHaveCount(0)
  const everything = await rows(page).count()
  // Up from the channels is the row of chips, on the one in force; along the row to Sports.
  await walk(page, 'ArrowUp', chip(page, 'All'))
  await walk(page, 'ArrowRight', sports(page))
  await page.keyboard.press('Enter')
  // The menu, on its first entry; nothing is chosen yet.
  await expect(entry(page, 'All Sports')).toHaveAttribute('data-focused', 'true')
  await expect(rows(page)).toHaveCount(everything)
  // Escape: closed, focus on the chip, the filter as it was.
  await page.keyboard.press('Escape')
  await expect(menu(page)).toHaveCount(0)
  await expect(sports(page)).toHaveAttribute('data-focused', 'true')
  await expect(chip(page, 'All')).toHaveAttribute('aria-pressed', 'true')
  await expect(rows(page)).toHaveCount(everything)
  await expect(page).toHaveURL(/\/live$/)

  await page.keyboard.press('Enter')
  await expect(entry(page, 'All Sports')).toHaveAttribute('data-focused', 'true')
  // Up from the first entry, and Left and Right anywhere, go nowhere.
  for (const key of ['ArrowUp', 'ArrowLeft', 'ArrowRight']) {
    await page.keyboard.press(key)
    await expect(entry(page, 'All Sports')).toHaveAttribute('data-focused', 'true')
  }
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('ArrowDown')
  await expect(entry(page, 'Football')).toHaveAttribute('data-focused', 'true')
  for (const key of ['ArrowLeft', 'ArrowRight']) {
    await page.keyboard.press(key)
    await expect(entry(page, 'Football')).toHaveAttribute('data-focused', 'true')
  }
  // Tab walks the entries and comes round again; it does not leave the menu.
  for (const stop of ['Hockey', 'All Sports', 'Basketball']) {
    await page.keyboard.press('Tab')
    await expect(entry(page, stop)).toHaveAttribute('data-focused', 'true')
  }
  await page.keyboard.press('Shift+Tab')
  await page.keyboard.press('Shift+Tab')
  await expect(entry(page, 'Hockey')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowDown')
  await expect(entry(page, 'Hockey')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowUp')
  await page.keyboard.press('Enter')
  await expect(menu(page)).toHaveCount(0)
  await expect(rows(page)).toHaveText([/Sunday League/])
  await expect(sports(page)).toHaveText('Sports · Football')
  await expect(sports(page)).toHaveAttribute('data-focused', 'true')

  // Open again: on the choice in force. Escape closes the list and changes nothing.
  await page.keyboard.press('Enter')
  await expect(entry(page, 'Football')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowDown')
  await expect(entry(page, 'Hockey')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Escape')
  await expect(menu(page)).toHaveCount(0)
  await expect(sports(page)).toHaveText('Sports · Football')
  await expect(sports(page)).toHaveAttribute('data-focused', 'true')
  await expect(rows(page)).toHaveText([/Sunday League/])

  // Tab along the row: News, Sports, Movies. Nothing stands between the chips; Shift+Tab back the same way.
  await walk(page, 'ArrowLeft', chip(page, 'News'))
  for (const stop of [sports(page), chip(page, 'Movies')]) {
    await page.keyboard.press('Tab')
    await expect(stop).toHaveAttribute('data-focused', 'true')
  }
  for (const stop of [sports(page), chip(page, 'News')]) {
    await page.keyboard.press('Shift+Tab')
    await expect(stop).toHaveAttribute('data-focused', 'true')
  }
  // Down from the chips still leaves the row for what is under it.
  await page.keyboard.press('ArrowDown')
  await expect(groups(page).locator('[data-focused="true"]')).toHaveCount(0)
  await expect(page.locator('.fx[data-focused="true"]')).not.toHaveCount(0)
})

test('the menu stays inside the window: moved left at the end of the scrolled row, above the chip when there is no room below', async ({ page }) => {
  await longRow(page)
  const regional = groups(page).getByRole('button', { name: /^Regional/ }), list = page.locator('.chip-menu')
  // The row overflows and the parent is its last chip, out of view until the row is scrolled to it.
  expect(await groups(page).evaluate((row) => row.scrollWidth > row.clientWidth)).toBe(true)
  await regional.click()
  await expect(menu(page, 'Regional')).toBeVisible()
  expect(await groups(page).evaluate((row) => row.scrollLeft)).toBeGreaterThan(0)
  const at = (await regional.boundingBox())!, box = (await list.boundingBox())!
  expect(at.x + at.width).toBeGreaterThan(1280 - 120)
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(1280)
  expect(box.x).toBeLessThan(at.x) // too wide to start at the chip's left edge
  expect(box.y).toBeGreaterThanOrEqual(at.y + at.height)
  // A long name is shortened on the chip and whole in its accessible name.
  await entry(page, 'The Longest Running Regional Network Of Them All').click()
  await expect(regional).toHaveAccessibleName('Regional · The Longest Running Regional Network Of Them All')
  expect(await regional.locator('.chip-menu-choice').evaluate((el) => el.scrollWidth > el.clientWidth && getComputedStyle(el).textOverflow === 'ellipsis')).toBe(true)
  expect((await regional.boundingBox())!.width).toBeLessThan(360)
  expect(await fits(page)).toBe(true)

  // A window too short for the list under the chip: it opens above, as tall as the room there, and scrolls inside itself.
  await page.setViewportSize({ width: 1280, height: 300 })
  await regional.click()
  await expect(menu(page, 'Regional')).toBeVisible()
  const chipAt = (await regional.boundingBox())!, above = (await list.boundingBox())!
  expect(above.y).toBeGreaterThanOrEqual(0)
  expect(above.y + above.height).toBeLessThanOrEqual(chipAt.y)
  expect(await list.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true)
  // The entry in force is brought into view inside it.
  await expect(entry(page, 'The Longest Running Regional Network Of Them All')).toBeInViewport()
  await page.keyboard.press('Escape')
  await expect(menu(page, 'Regional')).toHaveCount(0)
})

test.describe('on a phone', () => {
  test.use(PHONE)

  test('the chips stay one scrolling row and a parent opens its children in the bottom sheet', async ({ page }) => {
    await enter(page)
    await expect(rows(page).first()).toBeVisible()
    await expect(groups(page).getByRole('button')).toHaveText(TOP_LEVEL)
    await sports(page).tap()
    const sheet = page.getByRole('dialog', { name: 'Sports', exact: true })
    await expect(sheet).toBeVisible()
    expect(await names(entries(page))).toEqual(SPORTS_MENU)
    const box = (await sheet.boundingBox())!
    expect([box.x, box.width, Math.round(box.y + box.height)]).toEqual([0, 390, 844])
    expect(await fits(page)).toBe(true)
    await entry(page, 'All Sports').tap()
    await expect(sheet).toHaveCount(0)
    await expect(rows(page)).toHaveText(SPORTS)
    // The row is the same chips: nothing under it, nothing added to it.
    expect(await inRow(page)).toEqual(TOP_LEVEL)
    await expect(page.getByRole('button', { name: /clear/i })).toHaveCount(0)
    // (offsetTop: the chip in focus is drawn slightly larger.)
    expect(await groups(page).evaluate((row) => row.scrollWidth > row.clientWidth && new Set(Array.from(row.children, (el) => (el as HTMLElement).offsetTop)).size === 1)).toBe(true)
    const row = (await groups(page).boundingBox())!, first = (await rows(page).first().boundingBox())!
    expect(first.y - (row.y + row.height)).toBeLessThan(24)
    expect(await fits(page)).toBe(true)

    await sports(page).tap()
    await expect(entry(page, 'All Sports')).toHaveAttribute('aria-checked', 'true')
    await entry(page, 'Hockey').tap()
    await expect(rows(page)).toHaveText([/Old Time TV/])
    await expect(sports(page)).toHaveText('Sports · Hockey')
    await expect(sports(page)).toHaveAttribute('aria-pressed', 'true')
    expect(await inRow(page)).toEqual([...TOP_LEVEL.slice(0, 3), 'Sports · Hockey', ...TOP_LEVEL.slice(4)])
    expect(await fits(page)).toBe(true)

    await page.reload()
    await expect(sports(page)).toHaveText('Sports · Hockey')
    await expect(rows(page)).toHaveText([/Old Time TV/])
    // A tap outside the sheet closes it; All takes the filter off.
    await sports(page).tap()
    await expect(sheet).toBeVisible()
    await page.touchscreen.tap(195, 120)
    await expect(sheet).toHaveCount(0)
    await expect(rows(page)).toHaveText([/Old Time TV/])
    await chip(page, 'All').scrollIntoViewIfNeeded()
    await chip(page, 'All').tap()
    await expect(sports(page)).toHaveText('Sports')
    await expect(chip(page, 'All')).toHaveAttribute('aria-pressed', 'true')
    expect(await fits(page)).toBe(true)
  })
})

test('Settings lists a child by its path, and a hidden parent takes its children with it', async ({ page }) => {
  await enter(page, '/settings?tab=parental', 'owner')
  const shown = (name: string) => page.getByRole('button', { name, exact: true })
  for (const path of ['Sports', 'Sports / Basketball', 'Sports / Football', 'Sports / Hockey', 'News']) await expect(shown(`Shown · ${path}`)).toBeVisible()

  // A child alone.
  await shown('Shown · Sports / Football').click()
  await expect(shown('Hidden · Sports / Football')).toHaveAttribute('aria-pressed', 'true')
  await expect(shown('Shown · Sports')).toBeVisible()
  await shown('Hidden · Sports / Football').click()
  await expect(shown('Shown · Sports / Football')).toBeVisible()

  // The parent: its children are hidden with it and are not separate switches until it is shown again.
  await shown('Shown · Sports').click()
  await expect(shown('Hidden · Sports')).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('group-hidden-with-parent')).toHaveText(['Hidden with Sports · Basketball', 'Hidden with Sports · Football', 'Hidden with Sports · Hockey'])
  await page.getByLabel('Search channel groups').fill('sports / ho')
  await expect(page.getByTestId('group-hidden-with-parent')).toHaveText(['Hidden with Sports · Hockey'])
  await page.getByLabel('Search channel groups').fill('')

  await page.getByRole('link', { name: 'Live TV', exact: true }).click()
  await expect(rows(page).first()).toBeVisible()
  await expect(groups(page).getByRole('button')).toHaveText(TOP_LEVEL.filter((name) => name !== 'Sports'))
  await expect(rows(page).filter({ hasText: /Metro Sports 1|Court Vision|Sunday League/ })).toHaveCount(0)
})
