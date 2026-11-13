import { expect, test, type Locator, type Page } from '@playwright/test'

// Wide screens navigate from one bar across the top: every section named, nothing opening over the screen,
// and a remote reaches it with Up.
const GUEST = { id: 'p2', name: 'Guest', avatar: null, is_admin: false, theme_id: 'midnight', languages: ['eng', 'spa'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' }
const SECTIONS = ['Home', 'Live TV', 'Movies', 'Shows', 'My List']

/** Start signed in, past the first-run notice. */
async function enter(page: Page, path = '/') {
  await page.addInitScript((profile) => {
    sessionStorage.setItem('mock.accepted', JSON.stringify(['p1', 'p2', 'p3']))
    if (!localStorage.getItem('sh.session.v1')) localStorage.setItem('sh.session.v1', JSON.stringify({ access: `mock-access-${profile.id}`, refresh: `mock-refresh-${profile.id}`, profile }))
  }, GUEST)
  await page.goto(path)
}
const rail = (page: Page) => page.getByTestId('rail')
const rect = (target: Locator) => target.evaluate((e) => e.getBoundingClientRect().toJSON() as DOMRect)
const inBar = (page: Page) => rail(page).locator('[data-focused="true"]')
const inContent = (page: Page) => page.locator('main .fx[data-focused="true"]').last()
/** Up until focus is in the bar: a screen may have a row or two above the one focus starts on. */
async function upToBar(page: Page) {
  for (let i = 0; i < 4 && (await inBar(page).count()) === 0; i++) await page.keyboard.press('ArrowUp')
  await expect(inBar(page)).toHaveCount(1)
}
/** The bar's wordmark, entries and end buttons, left to right: one row inside the screen, none over another. */
async function expectOneRow(page: Page, width: number) {
  const bar = await rect(rail(page))
  expect([bar.x, bar.y, bar.width]).toEqual([0, 0, width])
  const parts = (await page.locator('[data-testid="rail"] > *').evaluateAll((all) => all.map((e) => e.getBoundingClientRect().toJSON() as DOMRect))).filter((p) => p.width > 0 && p.height > 0)
  expect(parts.length).toBeGreaterThanOrEqual(SECTIONS.length + 3)
  let edge = 0
  for (const part of parts) {
    expect(part.x).toBeGreaterThanOrEqual(edge - 0.5)
    expect(part.y).toBeGreaterThanOrEqual(0)
    expect(part.y + part.height).toBeLessThanOrEqual(bar.height)
    edge = part.x + part.width
  }
  expect(edge).toBeLessThanOrEqual(width)
  expect(new Set(parts.map((p) => Math.round(p.y + p.height))).size).toBe(1)
  return bar
}

for (const [width, height] of [[1920, 1080], [1024, 768]] as const) {
  test.describe(`${width}×${height}`, () => {
    test.use({ viewport: { width, height } })

    test('one row of names above the hero; Up and Down carry focus to the bar and back', async ({ page }) => {
      await enter(page)
      const play = page.getByTestId('hero-play')
      await expect(play).toHaveAttribute('data-focused', 'true')

      // Wordmark, the five sections by name, then Search, More and the profile; no hover, no focus needed.
      await expect(rail(page).getByText('StreamHub', { exact: true })).toBeVisible()
      await expect(rail(page).locator('.rail-item .lbl')).toHaveText(SECTIONS)
      for (const label of await rail(page).locator('.rail-item .lbl').all()) await expect(label).toBeVisible()
      for (const end of ['nav-search', 'nav-more', 'nav-profile']) await expect(page.getByTestId(end)).toBeVisible()
      await expect(page.getByTestId('nav-home')).toHaveAttribute('aria-current', 'page')
      const bar = await expectOneRow(page, width)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)

      // The hero starts under the bar, not behind it.
      expect((await rect(page.getByTestId('hero'))).y).toBeGreaterThanOrEqual(bar.height - 0.5)
      expect((await rect(page.getByTestId('hero-title'))).y).toBeGreaterThanOrEqual(bar.height)
      expect((await rect(play)).y).toBeGreaterThanOrEqual(bar.height)

      // Up: the entry for this section, ringed, the bar no larger and nothing laid over the screen.
      await page.keyboard.press('ArrowUp')
      const home = page.getByTestId('nav-home')
      await expect(home).toHaveAttribute('data-focused', 'true')
      await expect(home).not.toHaveCSS('outline-width', '0px')
      await expect(play).toHaveAttribute('data-focused', 'false')
      expect((await rect(rail(page))).height).toBe(bar.height)
      // Down: back on what was focused.
      await page.keyboard.press('ArrowDown')
      await expect(play).toHaveAttribute('data-focused', 'true')
      await expect(inBar(page)).toHaveCount(0)

      // Left and Right walk the bar and stop at its ends; Down from any entry returns to the content.
      await page.keyboard.press('ArrowUp')
      await page.keyboard.press('ArrowLeft')
      await expect(home).toHaveAttribute('data-focused', 'true')
      const order = ['nav-live-tv', 'nav-movies', 'nav-shows', 'nav-my-list', 'nav-search', 'nav-more', 'nav-profile']
      for (const next of order) {
        await page.keyboard.press('ArrowRight')
        await expect(page.getByTestId(next)).toHaveAttribute('data-focused', 'true')
        await expect(page.getByTestId(next)).not.toHaveCSS('outline-width', '0px')
      }
      await page.keyboard.press('ArrowRight')
      await expect(page.getByTestId('nav-profile')).toHaveAttribute('data-focused', 'true')
      await page.keyboard.press('ArrowDown')
      await expect(play).toHaveAttribute('data-focused', 'true')
      // Up again starts from the section being shown, not from where the last walk ended.
      await page.keyboard.press('ArrowUp')
      await expect(home).toHaveAttribute('data-focused', 'true')

      // Back leaves the bar for the content and goes nowhere.
      await page.keyboard.press('Escape')
      await expect(play).toHaveAttribute('data-focused', 'true')
      await expect(page).toHaveURL(/\/$/)
      await page.keyboard.press('ArrowUp')
      await page.keyboard.press('Backspace')
      await expect(play).toHaveAttribute('data-focused', 'true')
      await expect(page.getByTestId('home')).toBeVisible()
    })

    test('a screen without a hero: the bar hides nothing, and focus returns to the control it left', async ({ page }) => {
      await enter(page, '/browse/movies')
      const first = page.locator('[data-row="shelf:New Releases"] [data-testid="tile"]').first()
      await expect(first).toHaveAttribute('data-focused', 'true')
      const bar = await expectOneRow(page, width)
      await expect(page.getByTestId('nav-movies')).toHaveAttribute('aria-current', 'page')
      expect((await rect(page.getByRole('heading', { name: 'Movies', exact: true }))).y).toBeGreaterThanOrEqual(bar.height)

      // Up to the header, then to the bar: the Movies entry. Down returns to that header control.
      await page.keyboard.press('ArrowUp')
      await expect(page.locator('.browse-head [data-focused="true"]')).not.toHaveCount(0)
      await inContent(page).evaluate((e) => e.setAttribute('data-held', ''))
      await page.keyboard.press('ArrowUp')
      await expect(page.getByTestId('nav-movies')).toHaveAttribute('data-focused', 'true')
      await page.keyboard.press('ArrowRight')
      await expect(page.getByTestId('nav-shows')).toHaveAttribute('data-focused', 'true')
      await page.keyboard.press('ArrowDown')
      await expect(page.locator('[data-held]')).toHaveAttribute('data-focused', 'true')
      await expect(inBar(page)).toHaveCount(0)

      // Further down the page the focused poster and its shelf name scroll clear of the bar, never under it.
      for (let i = 0; i < 5; i++) await page.keyboard.press('ArrowDown')
      await expect(page.locator('[data-testid="tile"][data-focused="true"]')).toHaveCount(1)
      expect(await page.evaluate(() => scrollY)).toBeGreaterThan(0)
      for (let i = 0; i < 2; i++) {
        await page.keyboard.press('ArrowUp')
        expect((await rect(page.locator('[data-testid="tile"][data-focused="true"]'))).y).toBeGreaterThanOrEqual(bar.height)
      }
      expect((await rect(rail(page))).y).toBe(0)

      // A title page belongs to no section: Up from it lands on the entry it was reached from.
      await page.keyboard.press('Enter')
      await expect(page.getByTestId('title-detail')).toBeVisible()
      await expect(rail(page).locator('[aria-current="page"]')).toHaveCount(0)
      expect((await rect(page.getByTestId('title-detail').getByRole('heading').first())).y).toBeGreaterThanOrEqual(bar.height)
      await upToBar(page)
      await expect(page.getByTestId('nav-movies')).toHaveAttribute('data-focused', 'true')
    })
  })
}
test('from 721 px up the bar keeps to one row without sideways scroll; a narrow tablet gives up the wordmark, a phone keeps its bottom bar', async ({ page }) => {
  await enter(page, '/live?view=guide')
  await expect(page.getByTestId('guide')).toBeVisible()
  for (const width of [721, 800, 801, 1000, 1024, 1280, 1920]) {
    await page.setViewportSize({ width, height: 800 })
    await expectOneRow(page, width)
    await expect(rail(page).locator('.rail-item .lbl')).toHaveText(SECTIONS)
    for (const label of await rail(page).locator('.rail-item .lbl').all()) await expect(label).toBeVisible()
    for (const end of ['nav-search', 'nav-more', 'nav-profile']) await expect(page.getByTestId(end)).toBeVisible()
    await expect(rail(page).getByText('StreamHub', { exact: true })).toBeVisible({ visible: width > 800 })
    await expect(page.getByTestId('nav-live-tv')).toHaveAttribute('aria-current', 'page')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `no sideways scroll at ${width}`).toBe(true)
  }
  // One pixel narrower is a phone: five icons over their names along the bottom edge.
  await page.setViewportSize({ width: 720, height: 800 })
  await expect(rail(page).locator('.rail-item .lbl')).toHaveText(['Home', 'Search', 'Live TV', 'Library', 'More'])
  const bottom = await rect(rail(page))
  expect(Math.round(bottom.y + bottom.height)).toBe(800)
  expect(bottom.y).toBeGreaterThan(700)
  await expect(rail(page).getByText('StreamHub', { exact: true })).toHaveCount(0)
})
