import { expect, test, type Locator, type Page } from '@playwright/test'
import { asOwner, fits, listing } from './sports-helpers'

// The fixture's football games with team facts (src/mocks/sports.ts, every team made up), listed when
// sessionStorage['mock.sports'] is 'matchups': Lakeview at No. 12 Brookfield with art and live odds,
// No. 5 Harbor at Riverside without odds or art, a replay of No. 18 Northfield at Southgate, and
// games still to come.
async function open(page: Page) {
  await page.addInitScript(() => sessionStorage.setItem('mock.sports', 'matchups'))
  await asOwner(page)
  await page.goto('/sports')
}
const card = (page: Page, text: string) => page.getByTestId('sports-live').getByTestId('sports-listing').filter({ hasText: text })
const color = (el: Locator) => el.evaluate((node) => getComputedStyle(node).color)

test('a live football game is a matchup card: colours, ranking, records, VS., the clock and the odds', async ({ page }) => {
  await open(page)
  const game = card(page, 'Lakeview')
  await expect(game).toHaveCount(1)
  await expect(game).toHaveAttribute('data-matchup', 'true')
  await expect(game.getByTestId('matchup-away').getByTestId('matchup-name')).toHaveText('Lakeview')
  await expect(game.getByTestId('matchup-home').getByTestId('matchup-name')).toHaveText('Brookfield')
  expect(await color(game.getByTestId('matchup-away').getByTestId('matchup-name'))).toBe('rgb(43, 76, 154)')
  expect(await color(game.getByTestId('matchup-home').getByTestId('matchup-name'))).toBe('rgb(122, 31, 61)')
  // Only the ranked side wears a badge.
  await expect(game.getByTestId('matchup-home').getByTestId('matchup-rank')).toHaveText('#12')
  await expect(game.getByTestId('matchup-away').getByTestId('matchup-rank')).toHaveCount(0)
  await expect(game.getByTestId('matchup-away')).toContainText('6-3 · 4-1 Lakes League')
  await expect(game.getByTestId('matchup-home')).toContainText('8-1 · 5-1 Lakes League')
  await expect(game).toContainText('VS.')
  await expect(game.getByTestId('matchup-clock')).toHaveText(/^\d{1,2}:\d{2}\s?[AP]M · Q2 7:14$/)
  await expect(game).toContainText('LIVE')
  await expect(game).toContainText('VANTIX+ 041 · Ch 941')
  await expect(game).not.toContainText(/score/i)
  // The bar splits exactly by the odds, away colour from the left.
  const bar = game.getByTestId('matchup-odds')
  await expect(bar).toContainText('15%')
  await expect(bar).toContainText('85%')
  const [whole, away] = [await bar.boundingBox(), await game.getByTestId('matchup-odds-away').boundingBox()]
  expect(away!.width / whole!.width).toBeCloseTo(0.146, 2)
  expect(await game.getByTestId('matchup-odds-away').evaluate((node) => getComputedStyle(node).backgroundColor)).toBe('rgb(43, 76, 154)')
  expect(await bar.evaluate((node) => getComputedStyle(node).backgroundColor)).toBe('rgb(122, 31, 61)')
  // The art fades in over the colours once it has loaded.
  await expect(game.getByTestId('matchup-art')).toHaveAttribute('data-art', 'image')
  await expect(game.getByTestId('matchup-art').locator('img')).toHaveAttribute('data-loaded', 'true')
  await expect(game).toHaveAttribute('aria-label', /^Watch Lakeview Kestrels versus number 12 Brookfield Badgers, Q2 7:14, win probability Lakeview 15%, Brookfield 85%, College Football, live, on VANTIX\+ 041$/)
})

test('without odds there is no bar, and without art the two colours stand in', async ({ page }) => {
  await open(page)
  const game = card(page, 'Riverside')
  await expect(game.getByTestId('matchup-away').getByTestId('matchup-rank')).toHaveText('#5')
  await expect(game.getByTestId('matchup-home').getByTestId('matchup-rank')).toHaveCount(0)
  await expect(game.getByTestId('matchup-clock')).toContainText('Q1 3:52')
  await expect(game.getByTestId('matchup-odds')).toHaveCount(0)
  await expect(game).not.toContainText(/%|probability/i)
  const art = game.getByTestId('matchup-art')
  await expect(art).toHaveAttribute('data-art', 'colors')
  await expect(art.locator('img')).toHaveCount(0)
  const background = await art.evaluate((node) => getComputedStyle(node).backgroundImage)
  expect(background).toContain('linear-gradient(135deg')
  expect(background).toContain('rgb(224, 90, 27)')
  expect(background).toContain('rgb(15, 107, 92)')
})

test('a replay shows no clock and no odds', async ({ page }) => {
  await open(page)
  const game = card(page, 'Southgate')
  await expect(game).toContainText('Replay')
  await expect(game).not.toContainText('LIVE')
  await expect(game.getByTestId('matchup-home').getByTestId('matchup-name')).toHaveText('Southgate')
  await expect(game.getByTestId('matchup-clock')).toHaveText(/^\d{1,2}:\d{2}\s?[AP]M$/)
  await expect(game.getByTestId('matchup-odds')).toHaveCount(0)
  await expect(game).toHaveAttribute('aria-label', /^Watch number 18 Northfield Owls versus Southgate Foxes, College Football, replay, on VANTIX\+ 043$/)
})

test('other games keep their cards, and the remote moves over matchup cards as before', async ({ page }) => {
  await open(page)
  const live = page.getByTestId('sports-live').getByTestId('sports-listing')
  await expect(live).toHaveCount(5)
  // Not football: today's card, title and all.
  const rovers = listing(page, 'Riverton Rovers vs Harbor City')
  await expect(rovers).not.toHaveAttribute('data-matchup', 'true')
  await expect(rovers).toContainText(/Started \d{1,2}:\d{2}/)
  await expect(rovers).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowRight')
  await expect(card(page, 'Lakeview')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowLeft')
  await expect(rovers).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/play\/ch%3As_xt%3A9041$/)
})

test('a game to come shows its art or colours and each side ranking before its name', async ({ page }) => {
  await open(page)
  // Games to come are tiles in their sport's row, drawn like the live card.
  const coming = page.getByTestId('sports-row').filter({ has: page.getByRole('heading', { name: 'College Football' }) })
  const elks = coming.getByTestId('sports-listing').filter({ hasText: 'Eastgate' })
  await expect(elks.getByTestId('matchup-name')).toHaveText(['Eastgate', 'Westbrook'])
  await expect(elks.getByTestId('matchup-rank')).toHaveText(['#8', '#21'])
  await expect(elks.getByTestId('matchup-art')).toHaveAttribute('data-art', 'colors')
  await expect(elks).toHaveAttribute('aria-label', /^#8 Eastgate Elks at #21 Westbrook Wolves, College Football, (Today|Tomorrow) at /)
  const otters = coming.getByTestId('sports-listing').filter({ hasText: 'Riverside' })
  await expect(otters.getByTestId('matchup-name')).toHaveText(['Riverside', 'Lakeview'])
  await expect(otters.getByTestId('matchup-rank')).toHaveCount(0)
  await expect(otters.getByTestId('matchup-art')).toHaveAttribute('data-art', 'image')
  // Its sheet opens on the same card.
  await elks.click()
  const sheet = page.getByTestId('sports-sheet')
  await expect(sheet.getByTestId('sports-sheet-card').getByTestId('matchup-rank')).toHaveText(['#8', '#21'])
  await expect(sheet.getByTestId('sports-sheet-card')).toContainText(/Today|Tomorrow/)
  await page.keyboard.press('Escape')
  await expect(sheet).toHaveCount(0)
  // A sport chosen: the list, with each side's ranking before its name.
  await page.getByRole('button', { name: /^College Football · / }).click()
  const row = page.getByTestId('sports-upcoming').getByTestId('sports-listing').filter({ hasText: 'Eastgate Elks' })
  await expect(row).toContainText('#8Eastgate Elks at #21Westbrook Wolves')
})

for (const [width, height] of [[390, 844], [1280, 720], [1920, 1080]] as const) {
  test(`names fit on one line and nothing scrolls sideways at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height })
    await open(page)
    await expect(card(page, 'Lakeview').getByTestId('matchup-name')).toHaveCount(2)
    const overflow = await page.getByTestId('matchup-name').evaluateAll((names) => names.filter((n) => n.scrollWidth > n.clientWidth + 1).map((n) => n.textContent))
    expect(overflow).toEqual([])
    expect(await fits(page)).toBe(true)
  })
}

test('screenshot of the live matchup card for comparison with the approved mock', async ({ page }) => {
  const out = process.env.MATCHUP_SHOT
  test.skip(!out, 'set MATCHUP_SHOT to a file path to save the screenshot')
  await page.setViewportSize({ width: 1280, height: 900 })
  await open(page)
  const game = card(page, 'Lakeview')
  await expect(game.getByTestId('matchup-art').locator('img')).toHaveAttribute('data-loaded', 'true')
  await game.hover()
  await page.waitForTimeout(500)
  await game.screenshot({ path: out! })
})
