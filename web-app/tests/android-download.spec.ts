import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

test('Android download is remote-operable and preserves its filename', async ({ page }) => {
  await signIn(page)
  await page.route('**/downloads/streamhub-tv-debug-1b991e9.apk', route => route.fulfill({ status: 200, contentType: 'application/octet-stream', body: 'download-fixture' }))
  const button = page.getByTestId('android-download')
  await button.hover()
  await expect(button).toHaveAttribute('data-focused', 'true')
  const pending = page.waitForEvent('download')
  await page.keyboard.press('Enter')
  const download = await pending
  expect(download.suggestedFilename()).toBe('streamhub-tv-debug-1b991e9.apk')
  await expect(page.getByTestId('home')).toBeVisible()
})
