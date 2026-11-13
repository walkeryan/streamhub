import { expect, test } from '@playwright/test'
import { openMore, signIn } from './helpers'
import { fits, listing, stubSports, type StubEvent } from './sports-helpers'

const EVENTS: StubEvent[] = [
  { id: 'current', title: 'Blue Club vs Gold Club', sport: 'Soccer', startMin: -10, home: 'Blue Club', away: 'Gold Club', feeds: [{ id: 'A', number: 11, language: 'eng' }, { id: 'B', number: 12, quality: 'HD', language: 'fra' }] },
  // On the air by its guide entry, which does not call it live: no LIVE badge is claimed for it.
  { id: 'studio', title: 'Championship Review', sport: 'Tennis', startMin: -20, lengthMin: 80, live: false, feeds: [{ id: 'D', number: 14 }] },
  { id: 'upcoming', title: 'Upcoming matchup from guide', sport: 'Football', startMin: 120, feeds: [{ id: 'C', number: 13 }] },
  { id: 'ended', title: 'Earlier guide event', sport: 'Soccer', startMin: -120, lengthMin: 60, feeds: [{ id: 'E' }] },
]

test.beforeEach(async ({ page }) => { await stubSports(page, EVENTS) })

test('a live game on several channels asks which one, by remote, and plays the chosen channel', async ({ page }) => {
  await signIn(page)
  await page.goto('/sports')
  await expect(page.getByTestId('sports-listing')).toHaveCount(3)
  const game = listing(page, 'Blue Club vs Gold Club')
  await expect(game).toContainText('2 channels')
  await expect(game).toContainText('LIVE')
  await expect(game).toContainText('Started')
  await expect(listing(page, 'Championship Review')).toContainText('On now')
  await expect(listing(page, 'Championship Review')).not.toContainText('LIVE')
  await expect(page.getByTestId('sports').locator('img')).toHaveCount(0)
  await expect(listing(page, 'Championship Review')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowRight')
  await expect(game).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  const picker = page.getByTestId('sports-picker')
  await expect(picker.getByTestId('sports-feed')).toHaveCount(2)
  await expect(picker.getByTestId('sports-feed').nth(0)).toContainText('Channel A')
  await expect(picker.getByTestId('sports-feed').nth(0)).toContainText('Ch 11 · English')
  await expect(picker.getByTestId('sports-feed').nth(1)).toContainText('Ch 12 · French')
  await expect(picker.getByTestId('sports-feed').nth(1)).toContainText('HD')
  // Nothing about audio verification, and no button that repeats a channel's name.
  await expect(picker).not.toContainText(/audio|verif/i)
  await expect(picker.getByRole('button', { name: /^Watch on / })).toHaveCount(2)
  await expect(page.locator('.fx[data-focused="true"]')).toHaveCount(1)
  await expect(picker.getByTestId('sports-feed').nth(0)).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Escape')
  await expect(picker).toHaveCount(0)
  await expect(game).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  await expect(picker.getByTestId('sports-feed').nth(0)).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowDown')
  await expect(picker.getByTestId('sports-feed').nth(1)).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowUp')
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/play\/ch%3Afixture%3AA$/)
})

test('a game still to come offers its time and nothing to watch; what is over is not listed', async ({ page }) => {
  await signIn(page)
  await page.goto('/sports')
  await expect(page.getByText('Earlier guide event')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: /ended|earlier/i })).toHaveCount(0)
  await listing(page, 'Upcoming matchup from guide').click()
  const sheet = page.getByTestId('sports-sheet')
  await expect(sheet).toContainText(/(Today|Tomorrow) · \d{1,2}:\d{2}/)
  await expect(sheet).not.toContainText(/America\/|UTC|GMT/)
  await expect(sheet.getByRole('button', { name: /Watch/ })).toHaveCount(0)
  await expect(sheet).not.toContainText(/audio|verif|scheduled for later/i)
  // The title names no two sides, so recording offers this game and no team.
  await expect(sheet.getByRole('button')).toHaveText(['Record this game', 'Close'])
  await expect(page).not.toHaveURL(/\/play\//)
  await page.keyboard.press('Escape')
  await expect(listing(page, 'Upcoming matchup from guide')).toHaveAttribute('data-focused', 'true')
})

test('without a recorder the sheet of a coming game is its time and Close', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('mock.recordings.off', '1'))
  await signIn(page)
  await page.goto('/sports')
  await listing(page, 'Upcoming matchup from guide').click()
  const sheet = page.getByTestId('sports-sheet')
  await expect(sheet).toContainText(/(Today|Tomorrow) · \d{1,2}:\d{2}/)
  await expect(sheet.getByRole('button')).toHaveText(['Close'])
  await expect(sheet.getByRole('button', { name: 'Close' })).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Backspace')
  await expect(sheet).toHaveCount(0)
  await expect(listing(page, 'Upcoming matchup from guide')).toHaveAttribute('data-focused', 'true')
})

test('the day switch asks for that day in the viewer’s zone and never lists a day that has passed', async ({ page }) => {
  await signIn(page)
  await page.goto('/sports')
  await expect(page.getByTestId('sports-listing')).toHaveCount(3)
  const local = (days: number) => page.evaluate((days) => { const d = new Date(); d.setDate(d.getDate() + days); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` }, days)
  const asked = async () => { const q = await page.evaluate(() => sessionStorage.getItem('sports.query') ?? ''); const p = new URLSearchParams(q); return { date: p.get('date'), days: p.get('days'), status: p.get('status'), zone: p.get('timezone') } }
  const zone = await page.evaluate(() => Intl.DateTimeFormat().resolvedOptions().timeZone)
  expect(await asked()).toEqual({ date: await local(0), days: '2', status: 'active', zone })
  await expect(page.getByRole('button', { name: 'Previous day', exact: true })).toHaveAttribute('aria-disabled', 'true')
  await page.getByRole('button', { name: 'Tomorrow', exact: true }).click()
  await expect.poll(asked).toEqual({ date: await local(1), days: '1', status: 'active', zone })
  await expect(page.getByRole('button', { name: 'Tomorrow', exact: true })).toHaveAttribute('aria-pressed', 'true')
  // Another day has no Live now section: live is about this moment.
  await expect(page.getByTestId('sports-live')).toHaveCount(0)
  await page.getByRole('button', { name: 'Next day', exact: true }).click()
  await expect.poll(asked).toEqual({ date: await local(2), days: '1', status: 'active', zone })
  await expect(page.getByTestId('sports-date')).not.toHaveText(/Today|Tomorrow/)
  await expect(page.getByTestId('sports-upcoming')).toContainText('No games are listed for')
  await page.getByRole('button', { name: 'Previous day', exact: true }).click()
  await page.getByRole('button', { name: 'Today', exact: true }).click()
  await expect(page.getByTestId('sports-listing')).toHaveCount(3)
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await fits(page)).toBe(true)
})

test('sport chips keep every sport and its count while one is chosen', async ({ page }) => {
  await signIn(page)
  await page.goto('/sports')
  const chips = page.getByTestId('sports-chip')
  await expect(chips).toHaveText(['All · 3', 'Football · 1', 'Soccer · 1', 'Tennis · 1'])
  await page.getByRole('button', { name: 'Tennis · 1', exact: true }).click()
  await expect(page.getByTestId('sports-listing')).toHaveCount(1)
  await expect(chips).toHaveText(['All · 3', 'Football · 1', 'Soccer · 1', 'Tennis · 1'])
  await expect(page.getByRole('button', { name: 'Tennis · 1', exact: true })).toHaveAttribute('aria-pressed', 'true')
  // Nothing live in that sport: the screen says so and names what is next.
  await page.getByRole('button', { name: 'Football · 1', exact: true }).click()
  await expect(page.getByTestId('sports-live')).toContainText(/No games are live right now\. Next: Upcoming matchup from guide (tomorrow )?at \d/)
  await page.getByRole('button', { name: 'All · 3', exact: true }).click()
  await expect(page.getByTestId('sports-listing')).toHaveCount(3)
})

test('a game that ends while its channels are being chosen is no longer offered', async ({ page }) => {
  await signIn(page)
  await page.goto('/sports')
  await listing(page, 'Championship Review').hover()
  await listing(page, 'Blue Club vs Gold Club').click()
  await expect(page.getByTestId('sports-picker')).toBeVisible()
  await page.clock.install()
  await page.clock.fastForward(211 * 60_000)
  // The screen looks at the clock four times a minute.
  await expect(page.getByTestId('sports-picker')).toHaveCount(0, { timeout: 20_000 })
  await expect(page.getByRole('button', { name: /^Watch on / })).toHaveCount(0)
  await expect(page.locator('.fx[data-focused="true"]')).toHaveCount(1)
})

test('the first game has focus, and search and language are one remote press away', async ({ page }) => {
  await signIn(page)
  await page.goto('/sports')
  const first = listing(page, 'Championship Review')
  await expect(first).toHaveAttribute('data-focused', 'true')
  await expect(page.getByLabel('Search games, teams or channels')).toHaveCount(0)
  for (let step = 0; step < 6 && await page.getByTestId('sports-filters').getAttribute('data-focused') !== 'true'; step++) await page.keyboard.press(step < 3 ? 'ArrowUp' : 'ArrowRight')
  await expect(page.getByTestId('sports-filters')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('Enter')
  await expect(page.getByLabel('Search games, teams or channels')).toBeFocused()
  await expect(page.locator('.fx[data-focused="true"]')).toHaveCount(1)
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('sports-filters')).toHaveAttribute('data-focused', 'true')
  await page.setViewportSize({ width: 390, height: 844 })
  await first.hover()
  await page.evaluate(() => window.scrollTo(0, 0))
  const box = await first.boundingBox()
  expect(box!.y).toBeGreaterThanOrEqual(0)
  expect(box!.y + box!.height).toBeLessThanOrEqual(844 - 64)
})

test('finishing the first events request does not take focus from an opened search panel', async ({ page }) => {
  await signIn(page)
  await page.evaluate(() => { (window as unknown as { holdSports: boolean }).holdSports = true })
  await openMore(page, 'Sports')
  await expect.poll(() => page.evaluate(() => (window as unknown as { sportsPending?: boolean }).sportsPending)).toBe(true)
  await page.getByTestId('sports-filters').click()
  const search = page.getByLabel('Search games, teams or channels')
  await expect(search).toBeFocused()
  await page.evaluate(() => (window as unknown as { releaseSports: () => void }).releaseSports())
  await expect(page.getByTestId('sports-listing')).toHaveCount(3)
  await expect(search).toBeFocused()
  await expect(page.locator('.fx[data-focused="true"]')).toHaveCount(1)
})

test('Back from the player returns to the game that was played, not to the first card', async ({ page }) => {
  await signIn(page)
  await page.goto('/sports')
  // The second card, chosen in its picker: the first card is where focus would fall without a memory.
  const game = listing(page, 'Blue Club vs Gold Club')
  await game.click()
  await page.getByRole('button', { name: 'Watch on Channel B', exact: true }).click()
  await expect(page).toHaveURL(/\/play\/ch%3Afixture%3AB$/)
  await page.goBack()
  await expect(game).toHaveAttribute('data-focused', 'true')
  await expect(page.getByTestId('sports-picker')).toHaveCount(0)
})
