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

export const focusedLabel = (page: Page) => page.locator('.fx[data-focused="true"]').last().getAttribute('aria-label')
