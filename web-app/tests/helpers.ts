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

export const focusedLabel = (page: Page) => page.locator('.fx[data-focused="true"]').last().getAttribute('aria-label')
