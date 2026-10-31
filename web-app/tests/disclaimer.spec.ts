import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('disclaimer gate: Accept is disabled for 3 s and blocks the app until accepted', async ({ page }) => {
  await signIn(page, { accept: false })
  const accept = page.getByTestId('disclaimer-accept')
  await expect(page.getByText('StreamHub is a media player and organizer.')).toBeVisible()
  await expect(accept).toHaveAttribute('aria-disabled', 'true')
  // pressing Enter while disabled must not get past the gate
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('home')).toHaveCount(0)
  const t0 = Date.now()
  await expect(accept).not.toHaveAttribute('aria-disabled', 'true', { timeout: 6000 })
  expect(Date.now() - t0).toBeGreaterThan(1500) // delayed, not instant
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('home')).toBeVisible()
  // acceptance is remembered for the profile (stored server-side in the mock)
  await page.reload()
  await expect(page.getByTestId('disclaimer-gate')).toHaveCount(0)
})

test('disclaimer gate: Exit keeps the app locked', async ({ page }) => {
  await signIn(page, { accept: false })
  await page.getByTestId('disclaimer-exit').click()
  await expect(page.getByText('You declined the notice.')).toBeVisible()
  await expect(page.getByTestId('home')).toHaveCount(0)
})
