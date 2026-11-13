import { expect, test, type Locator, type Page } from '@playwright/test'

// Channel groups have two levels. The fixture's Sports has a channel of its own and three children;
// the other groups are plain. A guest sees four of its channels: one in each child, one in Sports itself.
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
const rows = (page: Page) => page.getByTestId('channel-row')
const picker = (page: Page) => page.getByTestId('live-subgroup')
const clear = (page: Page) => page.getByRole('button', { name: 'Clear filters', exact: true })
const list = (page: Page) => page.getByRole('dialog', { name: 'Sports', exact: true })
const fits = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
async function walk(page: Page, key: string, target: Locator, limit = 16) {
  for (let i = 0; i < limit && (await target.getAttribute('data-focused')) !== 'true'; i++) await page.keyboard.press(key)
  await expect(target).toHaveAttribute('data-focused', 'true')
}

test('chips are the top level; a parent offers its children in a drop-down beside its chip', async ({ page }) => {
  await enter(page)
  await expect(rows(page).first()).toBeVisible()
  await expect(groups(page).getByRole('button')).toHaveText(TOP_LEVEL)
  // Only the group that has children says so.
  await expect(chip(page, 'Sports').locator('svg')).toHaveCount(1)
  await expect(groups(page).locator('.chip svg')).toHaveCount(1)
  await expect(picker(page)).toHaveCount(0)

  await chip(page, 'Sports').click()
  await expect(rows(page)).toHaveText(SPORTS)
  await expect(picker(page)).toHaveText('All Sports')
  await expect(picker(page)).toHaveAccessibleName('Sports: All Sports')
  // Directly after the chip it belongs to, on the chips' line; then the small × that clears the filter.
  expect(await groups(page).evaluate((row) => Array.from(row.children, (el) => el.textContent))).toEqual([...TOP_LEVEL.slice(0, 4), 'All Sports', '', ...TOP_LEVEL.slice(4)])
  await expect(clear(page)).toHaveAccessibleName('Clear filters')
  expect((await clear(page).boundingBox())!.width).toBeLessThan(60)
  expect(await groups(page).evaluate((row) => new Set(Array.from(row.children, (el) => (el as HTMLElement).offsetTop + (el as HTMLElement).offsetHeight / 2)).size)).toBe(1)

  await picker(page).click()
  await expect(list(page).getByRole('button')).toHaveText(['All Sports (4)', 'Basketball (1)', 'Football (1)', 'Hockey (1)', 'Done'])
  await list(page).getByRole('button', { name: /^Football/ }).click()
  await expect(list(page)).toHaveCount(0)
  await expect(rows(page)).toHaveText([/Sunday League/])
  // The chip stays the choice; the drop-down names the child.
  await expect(chip(page, 'Sports')).toHaveAttribute('aria-pressed', 'true')
  await expect(picker(page)).toHaveText('Football')
  await expect(page.getByText('1 channels', { exact: true })).toBeVisible()

  // A plain group has no drop-down; the × moves to the chip now in force, and is gone with the filter.
  await chip(page, 'News').click()
  await expect(picker(page)).toHaveCount(0)
  await expect(chip(page, 'Sports')).toHaveAttribute('aria-pressed', 'false')
  expect(await groups(page).evaluate((row) => Array.from(row.children, (el) => el.textContent))).toEqual([...TOP_LEVEL.slice(0, 3), '', ...TOP_LEVEL.slice(3)])
  await chip(page, 'All').click()
  await expect(clear(page)).toHaveCount(0)
  await chip(page, 'News').click()
  expect(await fits(page)).toBe(true)
})

test('the group and the child survive a reload and Back, in the guide too, until Clear filters', async ({ page }) => {
  await enter(page)
  await chip(page, 'Sports').click()
  await picker(page).click()
  await list(page).getByRole('button', { name: /^Hockey/ }).click()
  await expect(rows(page)).toHaveText([/Old Time TV/])

  await page.reload()
  await expect(chip(page, 'Sports')).toHaveAttribute('aria-pressed', 'true')
  await expect(picker(page)).toHaveText('Hockey')
  await expect(rows(page)).toHaveText([/Old Time TV/])
  await expect(chip(page, 'All')).toHaveAttribute('aria-pressed', 'false')

  // The guide is filtered by the same choice: a child alone, then the parent with its children.
  await page.getByRole('button', { name: 'Guide', exact: true }).click()
  const guide = page.getByTestId('guide')
  await expect(guide.getByRole('button', { name: /^\d+ Old Time TV$/ })).toBeVisible()
  await expect(guide.getByRole('button', { name: /^\d+ Metro Sports 1$/ })).toHaveCount(0)
  await picker(page).click()
  await list(page).getByRole('button', { name: /^All Sports/ }).click()
  for (const name of ['Metro Sports 1', 'Court Vision', 'Sunday League', 'Old Time TV']) await expect(guide.getByRole('button', { name: new RegExp(`^\\d+ ${name}$`) })).toBeVisible()
  await expect(guide.getByRole('button', { name: /^\d+ Harbor News 24$/ })).toHaveCount(0)
  await page.getByRole('button', { name: 'Channels', exact: true }).click()
  await picker(page).click()
  await list(page).getByRole('button', { name: /^Basketball/ }).click()
  await expect(rows(page)).toHaveText([/Court Vision/])

  // Away and Back.
  await page.getByRole('link', { name: 'Home', exact: true }).click()
  await expect(page.getByTestId('home')).toBeVisible()
  await page.goBack()
  await expect(chip(page, 'Sports')).toHaveAttribute('aria-pressed', 'true')
  await expect(picker(page)).toHaveText('Basketball')
  await expect(rows(page)).toHaveText([/Court Vision/])

  await page.getByRole('button', { name: 'Clear filters', exact: true }).click()
  await expect(chip(page, 'All')).toHaveAttribute('aria-pressed', 'true')
  await expect(chip(page, 'Sports')).toHaveAttribute('aria-pressed', 'false')
  await expect(picker(page)).toHaveCount(0)
  await expect(rows(page).first()).toContainText('Harbor News 24')
  await page.reload()
  await expect(rows(page).first()).toContainText('Harbor News 24')
  await expect(chip(page, 'All')).toHaveAttribute('aria-pressed', 'true')
  await expect(picker(page)).toHaveCount(0)
})

test('by keyboard alone: arrows, Enter and Escape choose a child, and Tab walks the row in order', async ({ page }) => {
  await enter(page)
  await expect(rows(page).first()).toBeVisible()
  await expect(page.locator('.fx[data-focused="true"]')).not.toHaveCount(0)
  // Up from the channels is the row of chips, on the one in force; along the row to Sports.
  await walk(page, 'ArrowUp', chip(page, 'All'))
  await walk(page, 'ArrowRight', chip(page, 'Sports'))
  await page.keyboard.press('Enter')
  await expect(rows(page)).toHaveText(SPORTS)
  await expect(chip(page, 'Sports')).toHaveAttribute('data-focused', 'true')

  // The drop-down is the next stop to the right; Enter opens it on the current choice.
  await page.keyboard.press('ArrowRight')
  await expect(picker(page)).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  await expect(list(page).getByRole('button', { name: /^All Sports/ })).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('ArrowDown')
  await expect(list(page).getByRole('button', { name: /^Football/ })).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  await expect(list(page)).toHaveCount(0)
  await expect(rows(page)).toHaveText([/Sunday League/])
  await expect(picker(page)).toHaveText('Football')
  await expect(picker(page)).toHaveAttribute('data-focused', 'true')

  // Escape closes the list and changes nothing.
  await page.keyboard.press('Enter')
  await expect(list(page).getByRole('button', { name: /^Football/ })).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowDown')
  await expect(list(page).getByRole('button', { name: /^Hockey/ })).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Escape')
  await expect(list(page)).toHaveCount(0)
  await expect(picker(page)).toHaveText('Football')
  await expect(picker(page)).toHaveAttribute('data-focused', 'true')
  await expect(rows(page)).toHaveText([/Sunday League/])

  // Tab: News, Sports, its drop-down, the ×, Movies; Shift+Tab back the same way.
  await walk(page, 'ArrowLeft', chip(page, 'News'))
  for (const stop of [chip(page, 'Sports'), picker(page), clear(page), chip(page, 'Movies')]) {
    await page.keyboard.press('Tab')
    await expect(stop).toHaveAttribute('data-focused', 'true')
  }
  for (const stop of [clear(page), picker(page), chip(page, 'Sports'), chip(page, 'News')]) {
    await page.keyboard.press('Shift+Tab')
    await expect(stop).toHaveAttribute('data-focused', 'true')
  }
  // Down from the chips still leaves the row for what is under it.
  await page.keyboard.press('ArrowDown')
  await expect(groups(page).locator('[data-focused="true"]')).toHaveCount(0)
  await expect(page.locator('.fx[data-focused="true"]')).not.toHaveCount(0)
})

test.describe('on a phone', () => {
  test.use(PHONE)

  test('the chips stay one scrolling row and the drop-down takes the line under them', async ({ page }) => {
    await enter(page)
    await expect(rows(page).first()).toBeVisible()
    await expect(groups(page).getByRole('button')).toHaveText(TOP_LEVEL)
    await chip(page, 'Sports').tap()
    await expect(rows(page)).toHaveText(SPORTS)
    await expect(picker(page)).toHaveText('All Sports')
    // Not in the row: under it, inside the page's width. The row gains only the × beside the chip in force.
    await expect(groups(page).getByRole('button')).toHaveText([...TOP_LEVEL.slice(0, 4), '', ...TOP_LEVEL.slice(4)])
    // (offsetTop: the chip in focus is drawn slightly larger.)
    expect(await groups(page).evaluate((row) => row.scrollWidth > row.clientWidth && new Set(Array.from(row.children, (el) => (el as HTMLElement).offsetTop)).size === 1)).toBe(true)
    const row = (await groups(page).boundingBox())!, under = (await picker(page).boundingBox())!
    expect(under.y).toBeGreaterThanOrEqual(row.y + row.height - 8)
    expect(under.x).toBeGreaterThanOrEqual(0)
    expect(under.x + under.width).toBeLessThanOrEqual(390)
    expect(await fits(page)).toBe(true)

    await picker(page).tap()
    await expect(list(page).getByRole('button')).toHaveText(['All Sports (4)', 'Basketball (1)', 'Football (1)', 'Hockey (1)', 'Done'])
    expect(await fits(page)).toBe(true)
    await list(page).getByRole('button', { name: /^Hockey/ }).tap()
    await expect(rows(page)).toHaveText([/Old Time TV/])
    await expect(picker(page)).toHaveText('Hockey')
    await expect(chip(page, 'Sports')).toHaveAttribute('aria-pressed', 'true')
    expect(await fits(page)).toBe(true)

    await page.reload()
    await expect(picker(page)).toHaveText('Hockey')
    await expect(rows(page)).toHaveText([/Old Time TV/])
    await page.getByRole('button', { name: 'Clear filters', exact: true }).tap()
    await expect(picker(page)).toHaveCount(0)
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
