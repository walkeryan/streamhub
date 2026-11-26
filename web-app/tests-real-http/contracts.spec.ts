import { expect, test, type Page } from '@playwright/test'

async function login(page: Page, name = 'Fixture English') {
  await page.goto('/')
  await page.getByTestId('profile-card').filter({ hasText: name }).click()
  await expect(page.getByTestId('home')).toBeVisible()
}
async function bearer(page: Page) {
  return page.evaluate(() => JSON.parse(localStorage.getItem('sh.session.v1') ?? '{}').access as string)
}

test.beforeEach(async ({ context }) => {
  // Browser-side fail-closed egress too; API responses are never mocked/rewritten.
  await context.route('**/*', async (route) => {
    const url = new URL(route.request().url())
    if (url.origin === 'http://127.0.0.1:5187' || ['data:', 'blob:'].includes(url.protocol)) await route.continue()
    else await route.abort('blockedbyclient')
  })
})

test('real canonical search, unavailable detail, Back and warm response', async ({ page }) => {
  await login(page)
  await page.goto('/search')
  await page.getByRole('textbox', { name: 'Search', exact: true }).fill('Star Wars')
  const results = page.getByRole('region', { name: 'Movies', exact: true }).getByRole('link')
  await expect(results.first()).toHaveAccessibleName(/^Star Wars$/i)
  await expect(page.getByText('EN-NETFLIX-force-awakens')).toHaveCount(0)
  await page.getByRole('link', { name: 'Rogue One: A Star Wars Story', exact: true }).click()
  await expect(page.getByTestId('title-play')).toHaveCount(0)
  await expect(page.getByText('Not available from your sources.')).toBeVisible()
  await page.goBack()
  await expect(page.getByRole('textbox', { name: 'Search', exact: true })).toHaveValue('Star Wars')
  const headers = { Authorization: `Bearer ${await bearer(page)}` }
  const cold = await page.request.get('/api/v1/search?q=Star%20Wars', { headers })
  const warm = await page.request.get('/api/v1/search?q=Star%20Wars', { headers })
  expect(cold.ok()).toBeTruthy(); expect(warm.ok()).toBeTruthy()
  expect(await warm.json()).toEqual(await cold.json())
  expect(warm.headers()['cache-control']).toContain('private')
})

test('real version choice saves the explicitly chosen edition and resolves that file', async ({ page }) => {
  await login(page)
  const resolves: Record<string, unknown>[] = []
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/v1/playback/resolve') resolves.push(request.postDataJSON())
  })
  await page.goto('/title/tmdb%3Amovie%3A140607')
  await page.getByTestId('title-more').click()
  await page.getByTestId('title-source').click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('Audio & version')
  expect(resolves).toHaveLength(0)
  await dialog.getByRole('button', { name: /remember my next verified edition/i }).click()
  const resolved = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/v1/playback/resolve')
  await dialog.getByRole('button', { name: /FR-NETFLIX-force-awakens/i }).click()
  expect((await resolved).status()).toBe(200)
  expect(resolves[0]).toMatchObject({ id: 'tmdb:movie:140607', variant_id: 'lib:fixture-iptv:fr' })
  const saved = await page.request.get('/api/v1/playback/options?id=tmdb%3Amovie%3A140607', {
    headers: { Authorization: `Bearer ${await bearer(page)}` },
  })
  expect(await saved.json()).toMatchObject({ remembered: true, automatic_variant_id: 'lib:fixture-iptv:fr' })
  // Media decode is deliberately outside this contract fixture's claim.
})

test('real Sports, authentication and profile-isolated history/options', async ({ page }) => {
  await login(page, 'Fixture Clean')
  const english = { Authorization: `Bearer ${await bearer(page)}` }
  const sports = await page.request.get('/api/v1/sports', { headers: english })
  expect(sports.ok()).toBeTruthy()
  expect(JSON.stringify(await sports.json())).toContain('NBA: Fixture A vs Fixture B')
  await page.goto('/sports')
  await expect(page.getByText('NBA: Fixture A vs Fixture B').first()).toBeVisible()
  expect((await page.request.get('/api/v1/playback/options?id=ch%3Afixture-iptv%3Alive')).status()).toBe(401)
  const engOptions = await page.request.get('/api/v1/playback/options?id=tmdb%3Amovie%3A140607', { headers: english })
  expect(await engOptions.json()).toMatchObject({ remembered: false, automatic_variant_id: 'lib:fixture-iptv:en' })
  const frenchLogin = await page.request.post('/api/v1/auth/login', { data: { profile_id: 'fixture-french' } })
  expect(frenchLogin.ok()).toBeTruthy()
  const french = { Authorization: `Bearer ${(await frenchLogin.json()).access_token}` }
  const frOptions = await page.request.get('/api/v1/playback/options?id=tmdb%3Amovie%3A140607', { headers: french })
  expect((await frOptions.json()).automatic_variant_id).toBe('lib:fixture-iptv:fr')
  const engHistory = await page.request.get('/api/v1/history', { headers: english })
  const frHistory = await page.request.get('/api/v1/history', { headers: french })
  expect(await engHistory.json()).toEqual([])
  expect((await frHistory.json()).length).toBeGreaterThan(0)
})


test('appearance survives a new session and stays private to each profile', async ({ page, browser }) => {
  await login(page)
  await page.goto('/settings?tab=appearance')
  const choice = (p: Page, size: string) => p.getByRole('radiogroup', { name: 'Box art size' }).getByRole('radio', { name: size })
  const reply = page.waitForResponse((r) => r.request().method() === 'PATCH' && r.url().includes('/prefs'))
  await choice(page, 'Large').click()
  expect((await reply).status()).toBe(200)
  for (const [profile, size] of [['Fixture English', 'Large'], ['Fixture French', 'Standard']]) {
    const context = await browser.newContext({ baseURL: 'http://127.0.0.1:5187' })
    try {
      await context.route('**/*', (route) => new URL(route.request().url()).origin === 'http://127.0.0.1:5187' ? route.continue() : route.abort())
      const device = await context.newPage()
      await login(device, profile)
      await device.goto('/settings?tab=appearance')
      await expect(choice(device, size)).toHaveAttribute('aria-checked', 'true')
      await expect(choice(device, size)).not.toHaveAttribute('aria-disabled', 'true')
    } finally { await context.close() }
  }
})
