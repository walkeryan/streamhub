import { expect, type Page } from '@playwright/test'

/** Mock mode: sign in as the "Guest" profile (no PIN) and accept the first-run notice. */
export async function signIn(page: Page, { accept = true }: { accept?: boolean } = {}) {
  await page.goto('/')
  await expect(page.getByTestId('profile-picker')).toBeVisible()
  await page.getByTestId('profile-card').filter({ hasText: 'Guest' }).click()
  await expect(page.getByTestId('disclaimer-gate')).toBeVisible()
  if (accept) {
    const btn = page.getByTestId('disclaimer-accept')
    await expect(btn).toHaveAttribute('aria-disabled', 'true')
    await expect(btn).not.toHaveAttribute('aria-disabled', 'true', { timeout: 6000 })
    await page.keyboard.press('Enter')
    await expect(page.getByTestId('home')).toBeVisible()
  }
}

/** Sports, Requests, Sources and Settings live in the More menu. */
export async function openMore(page: Page, name: string) {
  await page.getByTestId('nav-more').click()
  await page.getByTestId('more-menu').getByRole('link', { name, exact: true }).click()
}

/** The metadata catalog (titles no source has) is reached from Search. */
export async function openDiscover(page: Page, section: 'movies' | 'shows' = 'movies') {
  await page.getByTestId('nav-search').click()
  await page.getByRole('button', { name: section === 'movies' ? 'Popular movies' : 'Popular shows', exact: true }).click()
  await expect(page.getByTestId('discover')).toBeVisible()
}

/** Movies and Shows keep sort, category, genre, source and language behind one Filter button. */
export async function openFilters(page: Page) {
  await page.getByTestId('browse-filter').click()
  await expect(page.getByTestId('browse-filters')).toBeVisible()
}
export async function closeFilters(page: Page) {
  await page.getByTestId('browse-filters').getByRole('button', { name: 'Done', exact: true }).click()
  await expect(page.getByTestId('browse-filters')).toHaveCount(0)
}
/** A filter in force is named under the search field, also while the Filter panel is closed. */
export const activeFilter = (page: Page, text: string) => page.getByTestId('active-filter').filter({ hasText: text })

/** Keyboard only: Up from the content reaches the top bar, Left/Right walk it to an entry. */
export async function focusBarEntry(page: Page, testId: string) {
  const bar = page.getByTestId('rail')
  for (let i = 0; i < 8 && (await bar.locator('[data-focused="true"]').count()) === 0; i++) await page.keyboard.press('ArrowUp')
  await expect(bar.locator('[data-focused="true"]')).toHaveCount(1)
  const item = page.getByTestId(testId)
  for (let i = 0; i < 8 && (await item.getAttribute('data-focused')) !== 'true'; i++) await page.keyboard.press('ArrowLeft')
  for (let i = 0; i < 8 && (await item.getAttribute('data-focused')) !== 'true'; i++) await page.keyboard.press('ArrowRight')
  await expect(item).toHaveAttribute('data-focused', 'true')
}

export const focusedLabel = (page: Page) => page.locator('.fx[data-focused="true"]').last().getAttribute('aria-label')

/** Live TV keeps what is rarely changed (the guide's schedules choice, language, region, source) behind one Options button. */
export async function openLiveOptions(page: Page) {
  await page.getByTestId('live-options').click()
  const sheet = page.getByTestId('live-options-sheet')
  await expect(sheet).toBeVisible()
  return sheet
}
export async function closeLiveOptions(page: Page) {
  await page.getByTestId('live-options-sheet').getByRole('button', { name: 'Done', exact: true }).click()
  await expect(page.getByTestId('live-options-sheet')).toHaveCount(0)
}
/** One choice in Live TV's Options, by the name on its chip. */
export async function chooseLiveOption(page: Page, ...names: string[]) {
  const sheet = await openLiveOptions(page)
  for (const name of names) await sheet.getByRole('button', { name, exact: true }).click()
  await closeLiveOptions(page)
}
/** Live TV's search is an icon until it is asked for, and a field for as long as it holds text. */
export async function searchLive(page: Page, text: string) {
  const field = page.getByLabel('Search channels or number')
  if (!(await field.count())) await page.getByTestId('live-search-open').click()
  await field.fill(text)
}
/** A choice made in Options that is not the usual one is named beside the Options button. */
export const liveChoice = (page: Page, text: string) => page.getByTestId('live-choice').filter({ hasText: text })
/** A Live TV group that has children is one chip: it opens a menu of "All <group>" and each child, and choosing there filters. */
export async function chooseLiveGroup(page: Page, group: string, choice = `All ${group}`) {
  await page.getByRole('group', { name: 'Channel groups', exact: true }).getByRole('button', { name: new RegExp(`^${group}( · |$)`) }).click()
  await page.getByRole('menu', { name: group, exact: true }).getByRole('menuitemradio', { name: new RegExp(`^${choice} \\(`) }).click()
  await expect(page.getByRole('menu')).toHaveCount(0)
}
