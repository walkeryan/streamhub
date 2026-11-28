import { expect, test, type Page } from '@playwright/test'
import { writeFileSync } from 'node:fs'

const dir = process.env.STREAMHUB_CARD_CAPTURE_DIR
const stage = process.env.STREAMHUB_CARD_CAPTURE_STAGE ?? 'after'

async function fixtures(page: Page) {
  await page.addInitScript(() => {
    const profile = { id: 'p1', name: 'Viewer', avatar: null, is_admin: true, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' }
    sessionStorage.setItem('mock.accepted', '["p1"]')
    localStorage.setItem('sh.session.v1', JSON.stringify({ access: 'mock-access-p1', refresh: 'mock-refresh-p1', profile }))
    const art = (h: string, accent: string) => 'data:image/svg+xml,' + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="600" height="900" viewBox="0 0 600 900"><defs><linearGradient id="sky" x2="0" y2="1"><stop stop-color="${h}"/><stop offset="1" stop-color="#101926"/></linearGradient></defs><rect width="600" height="900" fill="url(#sky)"/><circle fill="${accent}" cx="390" cy="230" r="100"/><path fill="${accent}" opacity=".24" d="M0 490l200-170 150 130 250-260v710H0z"/><path fill="#162433" d="M0 585l236-184 181 150 183-117v466H0z"/><path fill="#0c1921" d="M0 670l210-96 192 58 198-120v388H0z"/></svg>`)
    const names = ['Northbound', 'Cold Meridian', 'The Long Crossing', 'Signal at Dawn', 'After the Tide', 'Winter Lines']
    const colors = [['#245268','#e0ac72'],['#50364a','#dda38b'],['#255345','#a4c5ae'],['#594d39','#e3bd76']]
    const tiles = names.map((title,i) => {
      const color = colors[i % colors.length]!
      const image = art(color[0]!, color[1]!)
      return { id: 'tmdb:movie:' + (103 + i), kind: 'movie', title, year: 2023 - i, poster: image, backdrop: image,
        sources: i === 1 ? [] : [{ kind: 'library', source_type: 'plex', source_id: 'lib-fixture', source_name: 'Library', playable_in_app: true }],
        request_state: i === 1 ? 'approved' : null, request_progress: i === 1 ? { phase: 'searching' } : null, is_favorite: false }
    })
    const fast = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const request = new Request(input, init), url = new URL(request.url), path = decodeURIComponent(url.pathname)
      if (path === '/api/v1/search') return Response.json({ results: tiles, play_here: tiles.filter(t => t.sources.length), open_in_app: [], discovered: [], channels: [], programmes: [], actors: [], services: [], metadata_available: true })
      if (path === '/api/v1/watchlist' && request.method === 'GET') return Response.json([tiles[1]])
      if (path === '/api/v1/titles/tmdb:movie:104') return Response.json({ ...tiles[1], overview: 'A survey crew follows the last signal across a winter coastline.', runtime_min: 105, genres: ['Adventure'], cast: [], seasons: [], episodes: [], trailers: [], availability: [] })
      if (path.endsWith('/similar')) return Response.json({ id: tiles[1]!.id, tiles: tiles.filter((_, i) => i !== 1) })
      return fast(request)
    }
  })
}

const bounds = async (el: ReturnType<Page['locator']>) => (await el.boundingBox())!
const intersects = (a: { x:number; y:number; width:number; height:number }, b: typeof a) =>
  a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y

async function focus(page: Page, key: string) {
  await page.evaluate(async k => {
    const path = '/src/nav/spatial.ts'
    const nav = await import(path)
    await nav.setFocus(k)
  }, key)
  await expect(page.locator(`[data-focus-key="${key}"]`).first()).toHaveAttribute('data-focused', 'true')
}

for (const width of [1920, 390]) {
  test(`status, actions and recommendation captions stay distinct at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 1920 ? 1080 : 844 })
    await fixtures(page)
    await page.goto('/search?q=north')
    const cards = page.locator('.search-grid > .media-card')
    const first = cards.nth(0), second = cards.nth(1)
    const action = first.getByTestId('media-card-actions'), requestAction = second.getByTestId('media-card-actions')
    const available = first.getByRole('img', { name: 'Available to watch' })
    await expect(available).toBeVisible()
    await expect(second.getByTestId('tile-available')).toHaveCount(0)
    // A request says where it is on the tile itself; the menu button carries no status of its own.
    const phase = second.getByTestId('request-phase')
    await expect(phase).toHaveText('Searching')
    await expect(action.locator('.ds-glass-status')).toHaveCount(0)
    await expect(requestAction.locator('.ds-glass-status')).toHaveCount(0)
    // "Plays here" is the green tick inside the art's top right corner, never the accent of focus and progress.
    await expect(available).not.toHaveCSS('background-color', 'rgb(237, 155, 22)')
    const corner = await bounds(available), art = await bounds(first.getByTestId('tile'))
    expect(art.x + art.width - corner.x - corner.width).toBeGreaterThanOrEqual(0)
    expect(art.x + art.width - corner.x - corner.width).toBeLessThanOrEqual(12)
    expect(corner.y - art.y).toBeGreaterThanOrEqual(0)
    expect(corner.y - art.y).toBeLessThanOrEqual(12)
    // The button stays out of the picture until the card has the pointer or the focus.
    const idle = cards.nth(2)
    await expect(idle.getByTestId('tile')).toHaveAttribute('data-focused', 'false')
    await expect(idle.locator('.media-card-menu')).toHaveCSS('opacity', '0')
    const libraryOverlap = intersects(await bounds(action), corner)
    const requestOverlap = intersects(await bounds(requestAction), await bounds(phase))
    await focus(page, 'search::tmdb:movie:103')
    if (dir) await page.screenshot({ path: `${dir}/${stage}-search-${width}.png` })
    if (stage !== 'before') {
      await expect(first.locator('.media-card-menu')).toHaveCSS('opacity', '1')
      if (width === 1920) {
        // Up leaves the card for whatever is above it, never for the card's own button.
        await page.keyboard.press('ArrowUp')
        await expect(action).toHaveAttribute('data-focused', 'false')
        await expect(first.getByTestId('tile')).toHaveAttribute('data-focused', 'false')
        await focus(page, 'search::tmdb:movie:103')
        // Holding OK opens the menu; the key coming up afterwards presses nothing in it.
        await page.keyboard.down('Enter')
        await expect(page.getByText('Add to My List', { exact: true })).toBeVisible()
        await page.keyboard.up('Enter')
        await expect(page).toHaveURL(/\/search\?q=north$/)
      } else {
        await action.click()
      }
      await expect(page.getByText('Add to My List', { exact: true })).toBeVisible()
      await expect(page.getByRole('menuitem', { name: 'Open', exact: true })).toHaveAttribute('data-focused', 'true')
      await page.keyboard.press('Escape')
      // Focus goes back to what opened the menu: the card after a hold, the button after a click.
      await expect(width === 1920 ? first.getByTestId('tile') : action).toHaveAttribute('data-focused', 'true')
    }
    await page.goto('/title/tmdb%3Amovie%3A104')
    const watch = page.getByTestId('title-watchlist')
    const phaseLine = page.getByTestId('title-request-state')
    await expect(phaseLine).toContainText('Searching')
    await focus(page, 'title:watchlist')
    await watch.hover()
    await expect(watch).toHaveAttribute('aria-pressed', 'true')
    const tip = watch.locator('..').locator('.ds-glass-tip')
    const tipOverlap = await tip.isVisible() && intersects(await bounds(tip), await bounds(phaseLine))
    if (dir) await page.screenshot({ path: `${dir}/${stage}-title-${width}.png` })
    const rail = page.getByTestId('title-similar')
    const tile = rail.getByTestId('tile').first()
    await tile.scrollIntoViewIfNeeded()
    await focus(page, 'similar::tmdb:movie:103')
    await page.keyboard.press('ArrowRight')
    await expect(rail.getByTestId('tile').nth(1)).toHaveAttribute('data-focused', 'true')
    await page.keyboard.press('ArrowLeft')
    await expect(tile).toHaveAttribute('data-focused', 'true')
    const ringClipped = (item: typeof tile) => item.evaluate(async node => {
      await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
      await Promise.all(node.getAnimations().map(animation => animation.finished.catch(() => undefined)))
      const art = node.matches('.tp-like') ? node.querySelector('.tile-poster')! : node
      const style = getComputedStyle(art), card = art.getBoundingClientRect(), row = node.closest('.ds-row')!.getBoundingClientRect()
      const ring = parseFloat(style.outlineWidth) + parseFloat(style.outlineOffset)
      return card.top - ring < row.top - 1 || card.left - ring < Math.max(row.left, 0) - 1 || card.right + ring > Math.min(row.right, innerWidth) + 1 || card.bottom + ring > row.bottom + 1
    })
    const firstClipped = await ringClipped(tile)
    if (dir) await page.screenshot({ path: `${dir}/${stage}-similar-${width}.png` })
    const count = await rail.getByTestId('tile').count()
    for (let i = 1; i < count; i++) {
      await page.keyboard.press('ArrowRight')
      await expect(rail.getByTestId('tile').nth(i)).toHaveAttribute('data-focused', 'true')
    }
    const lastClipped = await ringClipped(rail.getByTestId('tile').last())
    const focusClipped = firstClipped || lastClipped
    if (dir) {
      await page.screenshot({ path: `${dir}/${stage}-similar-last-${width}.png` })
      writeFileSync(`${dir}/${stage}-geometry-${width}.json`, JSON.stringify({ libraryOverlap, requestOverlap, tipOverlap, focusClipped, firstClipped, lastClipped }))
    }
    if (stage === 'before') return
    expect(libraryOverlap).toBe(false)
    expect(requestOverlap).toBe(false)
    expect(tipOverlap).toBe(false)
    expect(focusClipped).toBe(false)
    await expect(tile.locator('.cap')).toHaveCount(0)
    await expect(tile).toContainText('Northbound')
    await expect(tile).toContainText('2023')
    const selected = rail.getByTestId('tile').last()
    const caption = await bounds(selected.locator('.ds-cap-below'))
    const poster = await bounds(selected.locator('.tile-poster'))
    expect(caption.y).toBeGreaterThan(poster.y + poster.height)
    expect(caption.y + caption.height).toBeLessThanOrEqual((await page.evaluate(() => innerHeight)) + 1)
  })
}

test.describe('a finger', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true })

  test('pressing and holding a card opens its menu, a tap opens the card, and no button sits on the picture', async ({ page }) => {
    await fixtures(page)
    await page.goto('/search?q=north')
    const card = page.locator('.search-grid > .media-card').nth(2), tile = card.getByTestId('tile')
    await expect(tile).toBeVisible()
    await expect(card.locator('.media-card-menu')).toHaveCSS('opacity', '0')
    await expect(card.locator('.media-card-menu')).toHaveCSS('pointer-events', 'none')
    const at = await bounds(tile)
    const point = { clientX: at.x + at.width / 2, clientY: at.y + at.height / 2, pointerType: 'touch', bubbles: true }
    const entry = page.getByRole('menuitem', { name: 'Thumbs up', exact: true })

    // A finger that moves is scrolling: nothing opens.
    await tile.dispatchEvent('pointerdown', point)
    await tile.dispatchEvent('pointermove', { ...point, clientY: point.clientY + 40 })
    await page.waitForTimeout(800)
    await expect(entry).toBeHidden()
    await tile.dispatchEvent('pointerup', point)

    // Held still, the menu opens, and lifting the finger afterwards is not a tap on the card.
    await tile.dispatchEvent('pointerdown', point)
    await expect(entry).toBeVisible()
    await tile.dispatchEvent('pointerup', point)
    await tile.dispatchEvent('click', { bubbles: true, cancelable: true })
    await expect(entry).toBeVisible()
    await expect(page).toHaveURL(/\/search\?q=north$/)
    await page.keyboard.press('Escape')
    await expect(entry).toBeHidden()

    await tile.tap()
    await expect(page).toHaveURL(/\/title\/tmdb%3Amovie%3A105$/)
  })
})
