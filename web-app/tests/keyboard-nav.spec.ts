import { expect, test } from '@playwright/test'
import { focusedLabel, signIn } from './helpers'

test('keyboard only: Home -> title -> player, Escape restores focus', async ({ page }) => {
  await signIn(page)
  // hero Play button holds focus on arrival
  await expect(page.locator('[data-testid="hero-play"]')).toBeVisible()
  await expect(page.locator('.fx[data-focused="true"]').first()).toContainText('Play')

  // Down into the first row (Continue Watching), then to the second tile and open it
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('ArrowRight')
  const tileLabel = await focusedLabel(page)
  expect(tileLabel).toBeTruthy()
  // focusing a poster updates the billboard
  await expect(page.getByTestId('hero-title')).toContainText(tileLabel!.replace(/\s*\(\d{4}\)$/, ''))
  await page.keyboard.press('Enter')

  await expect(page).toHaveURL(/\/title\//)
  await expect(page.getByTestId('title-detail')).toBeVisible()
  await expect(page.getByTestId('available-on')).toContainText('Availability data by JustWatch')
  await expect(page.locator('.fx[data-focused="true"]').first()).toContainText(/Play|Resume/)

  // Enter on Play starts the stream straight away
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/play\//)
  const video = page.getByTestId('video')
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing', { timeout: 15_000 })
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(0.3)

  // Space pauses, Escape leaves the player and returns to the title page
  await page.keyboard.press(' ')
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true)
  await page.keyboard.press('Escape')
  await expect(page).toHaveURL(/\/title\//)
  await page.keyboard.press('Escape')
  await expect(page).toHaveURL(/\/$/)
  // focus is restored to the tile that opened the title
  await expect.poll(() => focusedLabel(page)).toBe(tileLabel)
})

test('keyboard only: left from the first tile opens the rail, Enter navigates, Escape collapses', async ({ page }) => {
  await signIn(page)
  await expect(page.getByTestId('hero-play')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowLeft') // hero Play -> rail
  await expect(page.getByTestId('rail')).toHaveAttribute('data-expanded', 'true')
  await page.keyboard.press('ArrowDown') // Search
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/search/)
  await expect(page.getByLabel('Search titles and channels')).toBeFocused()
  await page.keyboard.type('harbor')
  await expect(page.getByTestId('group-play')).toBeVisible({ timeout: 8000 })
  await expect(page.getByTestId('justwatch-attribution')).toBeVisible()
})

test('live TV: zap with digits and Backspace in the player', async ({ page }) => {
  await signIn(page)
  await page.goto('/live')
  await expect(page.getByTestId('channel-row').first()).toBeVisible()
  await expect(page.getByLabel('Search channels or number')).toBeFocused()
  await page.getByTestId('channel-row').first().click()
  await expect(page).toHaveURL(/\/play\/ch/)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing', { timeout: 15_000 })
  const path = () => new URL(page.url()).pathname
  const first = path()
  await page.keyboard.press('ArrowUp')
  await expect.poll(path).not.toBe(first)
  await expect(page.getByTestId('player-title')).toHaveText('Metro Sports 1')
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  await page.keyboard.press('Backspace') // last-channel keyboard shortcut
  await expect.poll(path).toBe(first)
  await expect(page.getByTestId('player-title')).toHaveText('Harbor News 24')
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
  await page.keyboard.press('5')
  await expect(page.getByTestId('digits')).toHaveText('5')
  await expect.poll(path, { timeout: 5000 }).toContain('1005')
})

for (const key of ['Backspace', 'Escape', 'BrowserBack'] as const) {
  test(`rapid zap followed by ${key} preserves its distinct keyboard or remote meaning`, async ({ page }) => {
    await signIn(page)
    await page.goto('/live')
    await page.getByTestId('channel-row').first().click()
    await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
    const first = new URL(page.url()).pathname
    await page.evaluate((key) => {
      // Same task deliberately prevents React committing between the two keys.
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }))
      window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
    }, key)
    await expect.poll(() => new URL(page.url()).pathname).toBe(key === 'Backspace' ? first : '/live')
    if (key === 'Backspace') {
      await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
      await page.keyboard.press('m')
      await expect(page.getByTestId('player-menu')).toBeVisible()
      await page.keyboard.press('Backspace')
      await expect(page.getByTestId('player-menu')).toHaveCount(0)
      expect(new URL(page.url()).pathname).toBe(first)
    } else {
      await expect(page.getByLabel('Search channels or number')).toBeFocused()
    }
  })
}
