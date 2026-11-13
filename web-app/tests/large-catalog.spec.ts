import { expect, test } from '@playwright/test'
import { chooseLiveOption, closeLiveOptions, liveChoice, openLiveOptions, searchLive, signIn } from './helpers'

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
  // The list has no pages: it draws the rows near the view and loads the next forty as its end comes near.
  const list = page.getByTestId('channel-list'), channel = (n: number) => page.getByRole('option', { name: new RegExp(`^${n} \\w+ Channel ${n}(,|$)`) })
  await expect(channel(1)).toBeVisible()
  expect(await page.getByTestId('channel-row').count()).toBeLessThanOrEqual(30)
  await expect(page.getByText('34,911 channels', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /Next page|Previous page/ })).toHaveCount(0)
  // Language is one of the choices behind Options; the list under the sheet follows it.
  const options = await openLiveOptions(page)
  await options.getByRole('button', { name: 'English', exact: true }).click()
  await expect(page.getByTestId('channel-row').first()).toContainText('English Channel 1')
  await expect(page.getByTestId('channel-row').filter({ hasText: 'Spanish Channel' })).toHaveCount(0)
  await expect(options.getByRole('group', { name: 'Language', exact: true }).getByRole('button')).toHaveCount(4)
  await closeLiveOptions(page)
  // Not this profile's usual choice, so the header names it.
  await expect(liveChoice(page, 'English')).toBeVisible()
  await expect(page.getByText('17,456 channels', { exact: true })).toBeVisible()
  // The eighty-first English channel is on the third page.
  for (const first of [121, 241]) {
    await list.evaluate((el) => { el.scrollTop = el.scrollHeight })
    await expect(channel(first)).toBeVisible()
  }
  await channel(241).click()
  await expect(page.getByTestId('player')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('live')).toBeVisible()
  await expect(channel(241)).toHaveAttribute('data-focused', 'true')
  await expect(channel(241)).toBeInViewport()
  await expect(liveChoice(page, 'English')).toBeVisible()
  expect(await page.getByTestId('channel-row').count()).toBeLessThanOrEqual(30)
  await chooseLiveOption(page, 'All languages')
  // 804 groups: the first few are chips, the rest stay behind one searchable picker.
  expect(await page.getByRole('group', { name: 'Channel groups', exact: true }).getByRole('button').count()).toBeLessThanOrEqual(27)
  await page.getByRole('button', { name: 'More groups: All groups', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'More groups', exact: true })
  expect(await dialog.getByRole('button').count()).toBeLessThanOrEqual(43)
  await dialog.getByLabel('Search more groups').fill('Category 803')
  await dialog.getByRole('button', { name: /Category 803/ }).click()
  await expect(page.getByTestId('channel-row').first()).toContainText('Unknown Channel 804')
  expect(await page.getByTestId('channel-row').count()).toBeLessThanOrEqual(30)
  // A group is left by choosing All; the one × is the search field's.
  await expect(page.getByRole('button', { name: /clear/i })).toHaveCount(0)
  await page.getByRole('group', { name: 'Channel groups', exact: true }).getByRole('button', { name: 'All', exact: true }).click()
  await searchLive(page, '52366')
  await expect(page.getByTestId('channel-row')).toHaveCount(1)
  await expect(page.getByTestId('channel-row')).toContainText('Channel 52366')
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.setViewportSize({ width: 1280, height: 720 })
  await page.getByRole('button', { name: 'Clear search', exact: true }).click()
  await chooseLiveOption(page, 'My languages')
  await page.getByRole('button', { name: 'Guide', exact: true }).click()
  // The guide has no pages: it draws the rows near the view and loads the next forty as its end comes near.
  const guide = page.getByTestId('guide'), grid = guide.getByTestId('guide-grid')
  const channelCells = guide.getByRole('button', { name: /^\d+ (English|Spanish|Unknown) Channel/ })
  await expect(guide.getByRole('button', { name: '1 English Channel 1', exact: true })).toBeVisible()
  await expect(page.getByText('34,911 channels', { exact: true })).toBeVisible()
  expect(await channelCells.count()).toBeLessThanOrEqual(30)
  await expect(guide.getByRole('button', { name: /Next channels|Previous channels/ })).toHaveCount(0)
  // The forty-first channel of this profile's languages is on the second page.
  await grid.evaluate((el) => { el.scrollTop = el.scrollHeight })
  await expect(guide.getByRole('button', { name: '61 English Channel 61', exact: true })).toBeVisible()
  expect(await channelCells.count()).toBeLessThanOrEqual(30)
  await searchLive(page, '52366')
  await expect(channelCells).toHaveCount(1)
  expect(await grid.evaluate((el) => el.scrollTop)).toBe(0)
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
    // A library whose provider supplies no categories has no shelves: Movies opens on the grid.
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const request = new Request(input, init)
      return new URL(request.url).pathname === '/api/v1/browse/movies/shelves' ? Response.json({ shelves: [] }) : original(request)
    }
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
