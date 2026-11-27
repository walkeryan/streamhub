import { expect, test, type Page } from '@playwright/test'

const profile = { id: 'p1', name: 'Owner', avatar: null, is_admin: true, theme_id: 'midnight', languages: ['eng'], has_pin: false, kids: false, disclaimer_accepted_version: '1.0' }

async function signedIn(page: Page) {
  await page.addInitScript((current) => {
    sessionStorage.setItem('mock.accepted', JSON.stringify([current.id]))
    localStorage.setItem('sh.session.v1', JSON.stringify({ access: `mock-access-${current.id}`, refresh: `mock-refresh-${current.id}`, profile: current }))
  }, profile)
}

test('title actions fit phones while a portrait keeps its normal poster size', async ({ page }) => {
  await signedIn(page)
  await page.goto('/')
  await expect(page.getByTestId('home')).toBeVisible()
  await page.evaluate(() => {
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const response = await original(input, init)
      const url = input instanceof Request ? input.url : String(input)
      if (!decodeURIComponent(new URL(url).pathname).endsWith('/api/v1/titles/tmdb:movie:104') || !response.ok) return response
      const title = await response.clone().json()
      return new Response(JSON.stringify({ ...title, backdrop: null, progress_pct: 25 }), { status: response.status, headers: response.headers })
    }
    history.pushState({}, '', '/title/tmdb%3Amovie%3A104')
    dispatchEvent(new PopStateEvent('popstate'))
  })
  await expect(page.getByTestId('title-detail')).toBeVisible()

  for (const width of [320, 360, 390]) {
    await page.setViewportSize({ width, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    const poster = await page.locator('.tp-pic-art[data-shape="poster"]').boundingBox()
    expect(poster).not.toBeNull()
    const normalPosterWidth = await page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).fontSize) * 9)
    expect(poster!.width).toBeCloseTo(normalPosterWidth, 0)
    expect(poster!.height / poster!.width).toBeCloseTo(1.5, 1)
    const edges = await page.locator('.tp-actions [data-focus-key]').evaluateAll((controls) => controls.map((control) => {
      const rect = control.getBoundingClientRect()
      return { left: rect.left, right: rect.right }
    }))
    expect(edges.length).toBeGreaterThanOrEqual(5)
    expect(edges.every(({ left, right }) => left >= -1 && right <= width + 1)).toBe(true)
  }
})

test('Search, My List and Settings reflow at 200% text', async ({ page }) => {
  await signedIn(page)
  await page.setViewportSize({ width: 390, height: 844 })
  for (const route of ['/search', '/favorites', '/settings?tab=profiles']) {
    await page.goto(route)
    await page.locator('main').waitFor()
    await page.evaluate(() => { document.documentElement.style.fontSize = '200%' })
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  }
})

test('phone TV controls live in Profile settings and the paired session keeps polling across navigation', async ({ page }) => {
  await signedIn(page)
  await page.goto('/')
  await expect(page.getByTestId('home')).toBeVisible()
  await expect(page.getByTestId('rail').getByText('Watch on TV')).toHaveCount(0)
  await page.evaluate(() => { history.pushState({}, '', '/settings?tab=profiles'); dispatchEvent(new PopStateEvent('popstate')) })
  const entry = page.getByTestId('cast-settings')
  await entry.waitFor()
  await page.evaluate(() => {
    const original = window.fetch.bind(window)
    const session = { id: 'cast-1', target_name: 'Living Room TV', status: 'connected', playback: { id: null, title: '', poster: null, playing: false, position_ms: 0, duration_ms: null, live: false }, last_ack: 0, last_error: null }
    ;(window as unknown as { __castPolls: number }).__castPolls = 0
    window.fetch = async (input, init) => {
      const url = input instanceof Request ? input.url : String(input)
      if (url.includes('/api/v1/cast/session')) {
        ;(window as unknown as { __castPolls: number }).__castPolls++
        return Response.json({ session })
      }
      if (url.includes('/api/v1/cast/targets')) return Response.json({ targets: [] })
      return original(input, init)
    }
  })
  await entry.click()
  await expect(entry).toHaveText('TV controls for Living Room TV')
  await expect(page.getByRole('dialog', { name: 'Living Room TV' })).toBeVisible()
  await page.getByRole('button', { name: 'Continue browsing' }).click()
  const before = await page.evaluate(() => (window as unknown as { __castPolls: number }).__castPolls)
  await page.getByRole('link', { name: 'My List', exact: true }).click()
  await expect.poll(() => page.evaluate(() => (window as unknown as { __castPolls: number }).__castPolls), { timeout: 5000 }).toBeGreaterThan(before)
  await page.evaluate(() => { history.pushState({}, '', '/settings?tab=profiles'); dispatchEvent(new PopStateEvent('popstate')) })
  await expect(page.getByTestId('cast-settings')).toHaveText('TV controls for Living Room TV')
})

test('TV receiver keeps approval and command handling active away from Settings', async ({ page }) => {
  await signedIn(page)
  await page.addInitScript(() => {
    localStorage.setItem('sh.cast.receiver:p1', 'on')
    ;(window as unknown as { __castReceipt: { request_id?: string; result?: string } }).__castReceipt = {}
    ;(window as unknown as { StreamHubTV: Record<string, unknown> }).StreamHubTV = {
      play: () => true,
      signedOut: () => undefined,
      capabilities: () => JSON.stringify({ bridge: 1, shell: 1, native_playback: true }),
      deliverToken: () => undefined,
      castState: () => JSON.stringify((window as unknown as { __castReceipt: object }).__castReceipt),
      castCommand: (payload: string) => {
        const command = JSON.parse(payload)
        ;(window as unknown as { __castCommand?: { action: string } }).__castCommand = command
        ;(window as unknown as { __castReceipt: object }).__castReceipt = { request_id: command.request_id, result: 'ok' }
        return true
      },
    }
  })
  await page.goto('/')
  await expect(page.getByTestId('home')).toBeVisible()
  await page.evaluate(() => {
    let accepted = false
    let sent = false
    const original = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const url = input instanceof Request ? input.url : String(input)
      if (url.includes('/api/v1/cast/receiver/decision')) {
        const body = input instanceof Request ? await input.clone().json() : JSON.parse(String(init?.body))
        accepted = body.accept
        ;(window as unknown as { __castDecision?: boolean }).__castDecision = accepted
        return Response.json({ ok: true })
      }
      if (url.includes('/api/v1/cast/receiver/sync')) {
        const session = { id: 'receiver-1', target_name: 'This TV', status: accepted ? 'connected' : 'pending', playback: { id: null, title: '', poster: null, playing: false, position_ms: 0, duration_ms: null, live: false }, last_ack: 0, last_error: null }
        const command = accepted && !sent ? (sent = true, { seq: 1, request_id: 'request-1', action: 'play', session_id: 'receiver-1' }) : null
        return Response.json({ session, controller_name: 'Ryan’s phone', command })
      }
      return original(input, init)
    }
  })
  await expect(page.getByRole('dialog', { name: 'Allow this phone to control the TV?' })).toBeVisible()
  await page.getByRole('button', { name: 'Allow connection' }).click()
  await expect.poll(() => page.evaluate(() => (window as unknown as { __castDecision?: boolean }).__castDecision)).toBe(true)
  await expect.poll(() => page.evaluate(() => (window as unknown as { __castCommand?: { action: string } }).__castCommand?.action)).toBe('play')
  await expect(page.getByTestId('rail').getByText('TV controls')).toHaveCount(0)
  await page.evaluate(() => { history.pushState({}, '', '/settings?tab=profiles'); dispatchEvent(new PopStateEvent('popstate')) })
  await expect(page.getByTestId('cast-settings')).toHaveText('TV controls')
})
