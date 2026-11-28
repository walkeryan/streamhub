import { expect, test, type Page } from '@playwright/test'

// The title page of a series, a season and an episode (mock fixtures): where an episode sits, Back climbing
// to its season and show, the season strip, the household's activity and More like this.
const PROFILES = {
  owner: { id: 'p1', name: 'Owner', avatar: null, is_admin: true, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' },
  kids: { id: 'p3', name: 'Kids', avatar: null, is_admin: false, theme_id: 'oled', languages: ['eng'], has_pin: true, kids: true, disclaimer_accepted_version: '1.0' },
}
const SHOW = '/title/tmdb%3Atv%3A201'
const SEASON = '/title/tmdb%3Aseason%3A201%3A1'
const EPISODE = '/title/tmdb%3Aepisode%3A201%3A1%3A2'

async function enter(page: Page, path: string, who: keyof typeof PROFILES = 'owner') {
  await page.addInitScript((profile) => {
    sessionStorage.setItem('mock.accepted', JSON.stringify(['p1', 'p2', 'p3']))
    if (!localStorage.getItem('sh.session.v1')) localStorage.setItem('sh.session.v1', JSON.stringify({ access: `mock-access-${profile.id}`, refresh: `mock-refresh-${profile.id}`, profile }))
  }, PROFILES[who])
  await page.goto(path)
  await expect(page.getByTestId('title-detail')).toBeVisible()
}

test('an episode names its show and season, and Back climbs to the season, then the show', async ({ page }) => {
  await enter(page, EPISODE)
  const detail = page.getByTestId('title-detail')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Low Tide')
  await expect(page.getByTestId('title-show-link')).toHaveAttribute('role', 'link')
  await expect(detail.locator('.tp-sub')).toHaveText('Slack Water')
  await expect(page.getByTestId('title-season-link')).toHaveText('Season 1')
  await expect(detail.locator('.tp-se')).toContainText('Episode 2 of 6')
  await expect(detail.locator('.tp-rating')).toHaveText('TV-14')
  // The strip outlines this episode; the first one carries a watched tick.
  const strip = page.getByTestId('episodes')
  await expect(strip.getByTestId('episode')).toHaveCount(6)
  await expect(strip.locator('[aria-current="page"]')).toContainText('Slack Water')
  await expect(strip.getByTestId('episode').first().locator('.ds-tick')).toHaveCount(1)
  // Opened directly (no title page before it): Back goes up, not out.
  await page.keyboard.press('Escape')
  await expect(page).toHaveURL(new RegExp(`${SEASON}$`))
  await expect(page.getByTestId('title-detail').locator('.tp-sub')).toHaveText('Season 1')
  await page.keyboard.press('Escape')
  await expect(page).toHaveURL(new RegExp(`${SHOW}$`))
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Low Tide')
})

test('from the show, an episode opens its page and Back returns to the show; the links lead up too', async ({ page }) => {
  await enter(page, SHOW)
  const third = page.getByTestId('episodes').getByTestId('episode').nth(2)
  await expect(third).toHaveAttribute('href', '/title/tmdb%3Aepisode%3A201%3A1%3A3')
  const tabOpened = page.context().waitForEvent('page')
  await third.click({ modifiers: ['Control'] })
  const tab = await tabOpened
  await expect(tab).toHaveURL(/tmdb%3Aepisode%3A201%3A1%3A3$/)
  await tab.close()
  await expect(page).toHaveURL(new RegExp(`${SHOW}$`))
  await third.click()
  await expect(page).toHaveURL(/tmdb%3Aepisode%3A201%3A1%3A3$/)
  await expect(page.getByTestId('title-detail').locator('.tp-sub')).toHaveText('What the Net Held')
  await third.click()
  // The arrows only move between cards: holding OK opens the card's menu, on an entry that changes nothing,
  // and closing it puts focus back on the card.
  const opener = third.locator('..').getByTestId('media-card-actions')
  await page.keyboard.down('Enter')
  await expect(page.getByRole('menuitem', { name: 'Thumbs up', exact: true })).toBeVisible()
  await page.keyboard.up('Enter')
  await expect(page.getByRole('menuitem', { name: 'Open', exact: true })).toHaveAttribute('data-focused', 'true')
  await expect(opener).toHaveAttribute('aria-expanded', 'true')
  await page.keyboard.press('Escape')
  await expect(third).toBeFocused()
  await expect(opener).toHaveAttribute('aria-expanded', 'false')
  // Along the strip: the next episode replaces this one, so Back still returns to the show.
  await page.getByTestId('episodes').getByTestId('episode').nth(3).click()
  await expect(page).toHaveURL(/tmdb%3Aepisode%3A201%3A1%3A4$/)
  await page.keyboard.press('Escape')
  await expect(page).toHaveURL(new RegExp(`${SHOW}$`))
  // The season chip and the show name are links.
  await page.goto(EPISODE)
  await page.getByTestId('title-season-link').click()
  await expect(page).toHaveURL(new RegExp(`${SEASON}$`))
  await page.getByTestId('title-show-link').click()
  await expect(page).toHaveURL(new RegExp(`${SHOW}$`))
})

test('the remote reaches the show link and the season chip above the actions', async ({ page }) => {
  // The episode is navigable while its season and show requests are in flight.
  await page.addInitScript(() => {
    const fetch = window.fetch.bind(window)
    const waiting = new Promise<void>(resolve =>
      window.addEventListener('release-season', () => resolve(), { once: true }))
    window.fetch = async (input, init) => {
      const url = input instanceof Request ? input.url : String(input)
      if (/\/titles\/tmdb:(season|tv):201(?:[:/?]|$)/.test(decodeURIComponent(url))) await waiting
      return fetch(input, init)
    }
  })
  await enter(page, EPISODE)
  await expect(page.getByTestId('title-play')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowUp')
  await expect(page.getByTestId('title-season-link')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowUp')
  await expect(page.getByTestId('title-show-link')).toHaveAttribute('data-focused', 'true')
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('ArrowDown')
  await expect(page.getByTestId('title-play')).toHaveAttribute('data-focused', 'true')
  // Down from the actions reaches the season strip even when cards arrive after the keypress.
  await page.keyboard.press('ArrowDown')
  await page.evaluate(() => window.dispatchEvent(new Event('release-season')))
  await expect(page.getByTestId('episode').first()).toBeVisible()
  await expect(page.locator('[data-focused="true"]')).toHaveAttribute('data-focus-key', /^(season:|episodes::)/)
})

test('activity, cast and more like this; a kids profile sees only the kids', async ({ page, browser }) => {
  await enter(page, SHOW)
  const activity = page.getByTestId('title-activity')
  await expect(activity.getByTestId('activity-entry')).toHaveCount(3)
  await expect(activity.getByTestId('activity-entry').nth(0)).toContainText('You')
  await expect(activity.getByTestId('activity-entry').nth(0)).toContainText('Watching S1 · E2 · 33 min left')
  await expect(activity.getByTestId('activity-entry').nth(1)).toContainText('Finished Season 1')
  await expect(activity.getByTestId('activity-entry').nth(2)).toContainText('Added to My List')
  await expect(page.getByTestId('title-cast').getByRole('link')).toHaveCount(6)
  const similar = page.getByTestId('title-similar')
  await expect(similar.getByTestId('tile').first()).toBeVisible()
  await expect(similar.getByTestId('tile-available').first()).toBeVisible()
  // An episode's page shows the series' activity and recommendations.
  await page.goto(EPISODE)
  await expect(page.getByTestId('title-activity').getByTestId('activity-entry')).toHaveCount(3)

  const kids = await browser.newContext()
  const kid = await kids.newPage()
  await enter(kid, SHOW, 'kids')
  await expect(kid.getByTestId('title-activity').getByTestId('activity-entry')).toHaveCount(1)
  await expect(kid.getByTestId('title-activity')).toContainText('You')
  await expect(kid.getByTestId('title-activity')).not.toContainText('Guest')
  await kids.close()
})

test('mark watched and unwatched from the glass button', async ({ page }) => {
  await enter(page, '/title/tmdb%3Amovie%3A104')
  const watched = page.getByRole('button', { name: 'Mark watched', exact: true })
  await watched.click()
  const unwatch = page.getByRole('button', { name: 'Mark unwatched', exact: true })
  await expect(unwatch).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('title-detail').locator('.tp-pic-cap')).toHaveText('Watched')
  await unwatch.click()
  await expect(page.getByRole('button', { name: 'Mark watched', exact: true })).toHaveAttribute('aria-pressed', 'false')
})

test('remembering a non-default edition saves its own selected audio track', async ({ page }) => {
  await enter(page, '/title/tmdb%3Amovie%3A103?choose=1')
  await page.getByRole('button', { name: 'Remember my next verified edition and audio choice' }).click()
  await page.getByRole('button', { name: /^Audio for IPTV/ }).click()
  await page.getByRole('dialog').last().getByRole('button', { name: /French/ }).click()
  const saved = page.waitForRequest(req => req.method() === 'PUT' && req.url().includes('/playback/preference'))
  await page.locator('.source-choice').filter({ hasText: 'IPTV' }).click()
  const query = new URL((await saved).url()).searchParams
  expect(query.get('variant_id')).toBe('mock:s_xt:tmdb:movie:103')
  expect(query.get('audio_track')).toBe('1')
  await expect(page).toHaveURL(/audio=1/)
})
