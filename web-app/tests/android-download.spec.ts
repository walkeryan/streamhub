import { expect, test } from '@playwright/test'
import { openMore, signIn } from './helpers'

test('Android download serves the published TV build under its own filename', async ({ page }) => {
  await signIn(page)
  await page.route('**/downloads/streamhub-tv-1.2.1-8-debug.apk', route => route.fulfill({ status: 200, contentType: 'application/octet-stream', body: 'download-fixture' }))
  // The app download is not a content shortcut: it sits with the other app details.
  await expect(page.getByTestId('android-download')).toHaveCount(0)
  await openMore(page, 'Settings')
  await page.getByTestId('tab-about').click()
  await expect(page.getByTestId('about')).toContainText('Version 1.2.1')
  const button = page.getByTestId('android-download')
  await button.hover()
  await expect(button).toHaveAttribute('data-focused', 'true')
  const pending = page.waitForEvent('download')
  await page.keyboard.press('Enter')
  const download = await pending
  expect(download.suggestedFilename()).toBe('streamhub-tv-1.2.1-8-debug.apk')
  await expect(page.getByTestId('about')).toBeVisible()
})

test('no published TV build: no download button, and the page says so', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('mock.tv-release', 'none'))
  await signIn(page)
  await openMore(page, 'Settings')
  await page.getByTestId('tab-about').click()
  await expect(page.getByTestId('android-unpublished')).toHaveText('No TV app is published on this server.')
  await expect(page.getByTestId('android-download')).toHaveCount(0)
})
