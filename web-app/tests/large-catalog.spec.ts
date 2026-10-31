import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

// Match the order of magnitude of the user's imported catalog without touching real providers.
test('52k channels stay bounded, searchable and explicitly language-selectable', async ({ page }) => {
  await signIn(page)
  await page.evaluate(async () => {
    const path = '/src/mocks/fixtures.ts'
    const { channels } = await import(/* @vite-ignore */ path)
    const base = channels[0]
    channels.splice(0, channels.length, ...Array.from({ length: 52366 }, (_, i) => ({
      ...base, id: `ch:large:${i}`, number: i + 1, ext_id: String(i),
      name: `${['English', 'Spanish', 'Unknown'][i % 3]} Channel ${i + 1}`,
      language: ['eng', 'spa', 'und'][i % 3], group: `Category ${String(i % 804).padStart(3, '0')}`,
      now: null, next: null,
    })))
  })
  await page.getByRole('link', { name: 'Live TV', exact: true }).click()
  await expect(page.getByTestId('channel-row')).toHaveCount(80)
  await page.getByRole('button', { name: 'English', exact: true }).click()
  await expect(page.getByTestId('channel-row').first()).toContainText('English Channel 1')
  await expect(page.getByTestId('channel-row').filter({ hasText: 'Spanish Channel' })).toHaveCount(0)
  await expect(page.getByRole('group', { name: 'Language', exact: true }).getByRole('button')).toHaveCount(4)
  await page.getByRole('button', { name: 'Next page', exact: true }).click()
  await expect(page.getByTestId('channel-row').first()).toContainText('English Channel 241')
  await page.getByTestId('channel-row').first().click()
  await expect(page.getByTestId('player')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('live')).toBeVisible()
  await expect(page.getByTestId('channel-row').first()).toContainText('English Channel 241')
  await expect(page.getByTestId('channel-row').first()).toHaveAttribute('data-focused', 'true')
  await expect(page.getByRole('button', { name: 'English', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('channel-row')).toHaveCount(80)
  await page.getByRole('button', { name: 'All languages', exact: true }).click()
  await page.getByRole('button', { name: 'Category: All categories', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Category', exact: true })
  expect(await dialog.getByRole('button').count()).toBeLessThanOrEqual(43)
  await dialog.getByLabel('Search category').fill('Category 803')
  await dialog.getByRole('button', { name: /Category 803/ }).click()
  await expect(page.getByTestId('channel-row').first()).toContainText('Unknown Channel 804')
  expect(await page.getByTestId('channel-row').count()).toBeLessThanOrEqual(80)
  await page.getByRole('button', { name: 'Clear filters', exact: true }).click()
  await page.getByLabel('Search channels or number').fill('52366')
  await expect(page.getByTestId('channel-row')).toHaveCount(1)
  await expect(page.getByTestId('channel-row')).toContainText('Channel 52366')
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.setViewportSize({ width: 1280, height: 720 })
  await page.getByRole('button', { name: 'Clear filters', exact: true }).click()
  await page.getByRole('button', { name: 'My languages', exact: true }).click()
  await page.getByRole('button', { name: 'Guide', exact: true }).click()
  const guide = page.getByTestId('guide')
  await expect(guide.getByRole('button', { name: /^\d+ (English|Spanish|Unknown) Channel/ })).toHaveCount(40)
  await guide.getByRole('button', { name: 'Next channels', exact: true }).click()
  await expect(guide.getByRole('button', { name: '61 English Channel 61', exact: true })).toBeVisible()
  await page.getByLabel('Search channels or number').fill('52366')
  await expect(guide.getByRole('button', { name: /^\d+ (English|Spanish|Unknown) Channel/ })).toHaveCount(1)
})

test('profile accepted on another device replaces the stale cached notice gate', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('sh.session.v1', JSON.stringify({
      access: 'mock-access-p2', refresh: 'mock-refresh-p2',
      profile: { id: 'p2', name: 'Guest', is_admin: false, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids: false, disclaimer_accepted_version: null },
    }))
    sessionStorage.setItem('mock.accepted', JSON.stringify(['p2']))
  })
  await page.goto('/')
  await expect(page.getByTestId('home')).toBeVisible()
  await expect(page.getByTestId('disclaimer-gate')).toHaveCount(0)
})


test('211k library titles render only one server page and remain searchable', async ({ page }) => {
  await signIn(page)
  await page.evaluate(async () => {
    const path = '/src/mocks/fixtures.ts'
    const { titles } = await import(/* @vite-ignore */ path)
    const base = titles.find((t: {kind: string}) => t.kind === 'movie')
    titles.splice(0, titles.length)
    for (let i = 1; i <= 211613; i++) titles.push({ ...base, id: `catalog:${i}`, title: `Catalog Movie ${i}` })
  })
  await page.getByRole('link', { name: 'Movies', exact: true }).click()
  const browse = page.getByTestId('browse')
  await expect(browse.getByTestId('tile')).toHaveCount(60)
  await browse.getByRole('button', { name: 'Next page', exact: true }).click()
  await expect(browse.getByTestId('tile').first()).toHaveAttribute('aria-label', 'Catalog Movie 61')
  await browse.getByTestId('tile').first().click()
  await expect(page).toHaveURL(/\/title\//)
  await page.keyboard.press('Escape')
  await expect(browse.getByTestId('tile').first()).toHaveAttribute('aria-label', 'Catalog Movie 61')
  await expect(browse.getByTestId('tile').first()).toHaveAttribute('data-focused', 'true')
  await browse.getByLabel('Search this library').fill('Catalog Movie 211613')
  await expect(browse.getByTestId('tile')).toHaveCount(1)
  await expect(browse.getByTestId('tile')).toHaveAttribute('aria-label', 'Catalog Movie 211613')
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})
