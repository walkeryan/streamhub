import { expect, test } from '@playwright/test'

test('paired receiver accepts a phone, plays remotely, and reports transport state', async ({ page, browser }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await page.getByTestId('profile-card').filter({ hasText: 'Fixture English' }).click()
  await expect(page.getByTestId('home')).toBeVisible()
  const access = await page.evaluate(() => JSON.parse(localStorage.getItem('sh.session.v1')!).access)
  expect(
    (
      await page.request.patch('/api/v1/profiles/fixture-english/prefs', {
        headers: { Authorization: 'Bearer ' + access },
        data: { 'idleSignOutMinutes.v1': 1 },
      })
    ).ok(),
  ).toBeTruthy()
  const hello = await page.request.post('/api/v1/auth/device')
  const code = await hello.json()
  const approval = await page.request.post('/api/v1/auth/device/approve', {
    headers: { Authorization: 'Bearer ' + access },
    data: { user_code: code.user_code },
  })
  expect(approval.status()).toBe(204)
  const token = await page.request.post('/api/v1/auth/device/poll', { data: { device_code: code.device_code } })
  expect(token.status()).toBe(200)
  const tokens = await token.json()
  const tv = await browser.newContext({
    baseURL: 'http://127.0.0.1:5187',
    viewport: { width: 1920, height: 1080 },
  })
  try {
    // Substitute the Android bridge only. Pairing, authorization, commands, acknowledgments,
    // profile data and media bytes travel over real HTTP between separate clients.
    await tv.addInitScript(
      ({ access, refresh, profile }) => {
        localStorage.setItem('sh.session.v1', JSON.stringify({ access, refresh, profile }))
        localStorage.setItem('sh.cast.receiver:' + profile.id, 'on')
        let id: string | null = null
        let request = ''
        let video: HTMLVideoElement | null = null
        Object.assign(window, {
          StreamHubTV: {
            play: () => false,
            signedOut: () => {},
            deliverToken: () => {},
            capabilities: () => '{"native_playback":true}',
            castState: () =>
              JSON.stringify({
                v: 1,
                kind: 'mini_player',
                request_id: request,
                result: 'ok',
                state:
                  id && video
                    ? {
                        id,
                        title: 'Fixture film',
                        position_ms: Math.round(video.currentTime * 1000),
                        duration_ms: Number.isFinite(video.duration) ? Math.round(video.duration * 1000) : 10000,
                        playing: !video.paused,
                        live: false,
                        has_next: false,
                        has_previous: false,
                      }
                    : null,
              }),
            castCommand: (payload: string) => {
              const c = JSON.parse(payload)
              if (c.action === 'load') {
                id = c.id
                video = document.createElement('video')
                video.muted = true
                video.loop = true
                video.src = '/appearance/sample.mp4'
                video.dataset.testid = 'receiver-video'
                document.body.append(video)
                void video.play().then(() => {
                  request = c.request_id
                })
              } else if (c.action === 'pause') {
                video!.pause()
                request = c.request_id
              } else if (c.action === 'play') {
                void video!.play().then(() => {
                  request = c.request_id
                })
              } else if (c.action === 'seek') {
                video!.currentTime = c.position_ms / 1000
                request = c.request_id
              } else if (c.action === 'stop') {
                video?.remove()
                video = null
                id = null
                request = c.request_id
              }
              return true
            },
          },
        })
      },
      { access: tokens.access_token, refresh: tokens.refresh_token, profile: tokens.profile },
    )
    const receiver = await tv.newPage()
    await receiver.goto('/')
    await expect(receiver.getByTestId('home')).toBeVisible()
    await page.getByRole('button', { name: 'Choose where to watch' }).click()
    await page.getByRole('button', { name: 'TV', exact: true }).click()
    await receiver.getByRole('button', { name: 'Allow connection' }).click()
    await page.getByRole('button', { name: 'Continue browsing' }).click()
    await page.goto('/title/tmdb%3Amovie%3A140607')
    await page.getByTestId('title-play').click()
    await expect(receiver.getByTestId('receiver-video')).toBeVisible({ timeout: 20000 })
    await receiver.evaluate(() => {
      const key = Object.keys(localStorage).find((k) => k.startsWith('sh.idle.v1:'))
      if (!key) throw new Error('The configured receiver timeout was not loaded')
      localStorage.setItem(key, String(Date.now() - 65_000))
      window.dispatchEvent(new Event('pageshow'))
    })
    await expect
      .poll(() =>
        receiver.evaluate(
          () =>
            Number(localStorage.getItem(Object.keys(localStorage).find((k) => k.startsWith('sh.idle.v1:'))!)) >
            Date.now() - 5000,
        ),
      )
      .toBe(true)
    await expect(page).not.toHaveURL(/\/play\//)
    await page.getByRole('button', { name: /TV controls for/ }).click()
    await page.getByRole('button', { name: 'Pause', exact: true }).click()
    await expect
      .poll(() => receiver.getByTestId('receiver-video').evaluate((v) => (v as HTMLVideoElement).paused))
      .toBe(true)
    await expect(page.getByRole('button', { name: 'Play', exact: true })).toBeEnabled()
    await page.getByRole('button', { name: 'Disconnect phone' }).click()
    await expect(page.getByRole('button', { name: 'Disconnect phone' })).toHaveCount(0)
  } finally {
    await tv.close()
  }
})

test('an idle shared TV returns to the real profile picker without forgetting its paired session or PIN', async ({ page, browser }) => {
  await page.goto('/')
  await page.getByTestId('profile-card').filter({ hasText: 'Admin' }).first().click()
  await page.getByLabel('Password', { exact: true }).fill('fixture-only-admin-password')
  await page.keyboard.press('Enter')
  await page.getByRole('button', { name: 'I accept', exact: true }).click()
  await expect(page.getByTestId('home')).toBeVisible()
  const auth = await page.evaluate(() => JSON.parse(localStorage.getItem('sh.session.v1')!).access)
  expect((await page.request.patch('/api/v1/profiles/fixture-clean/prefs', {
    headers: { Authorization: 'Bearer ' + auth }, data: { 'idleSignOutMinutes.v1': 1 },
  })).ok()).toBe(true)
  expect((await page.request.put('/api/v1/profiles/fixture-clean/pin', {
    headers: { Authorization: 'Bearer ' + auth }, data: { pin: '2468', kind: 'pin' },
  })).status()).toBe(204)
  const hello = await page.request.post('/api/v1/auth/device')
  const code = await hello.json()
  expect((await page.request.post('/api/v1/auth/device/approve', {
    headers: { Authorization: 'Bearer ' + auth },
    data: { user_code: code.user_code, profile_id: 'fixture-clean', shared_device: true },
  })).status()).toBe(204)
  const token = await page.request.post('/api/v1/auth/device/poll', { data: { device_code: code.device_code } })
  expect(token.status()).toBe(200)
  const tokens = await token.json()
  const tv = await browser.newContext({ baseURL: 'http://127.0.0.1:5187', viewport: { width: 1920, height: 1080 } })
  try {
    await tv.addInitScript(({ access, refresh, profile }) => {
      localStorage.setItem('sh.session.v1', JSON.stringify({ access, refresh, profile }))
      Object.assign(window, { StreamHubTV: { signedOut: () => {}, deliverToken: () => {}, capabilities: () => '{}' } })
    }, { access: tokens.access_token, refresh: tokens.refresh_token, profile: tokens.profile })
    const receiver = await tv.newPage()
    await receiver.goto('/')
    const picker = receiver.getByTestId('who-is-watching')
    const current = picker.getByTestId('who-card').filter({ hasText: 'Fixture Clean' })
    const enterPin = async () => {
      await current.click()
      const pin = receiver.getByTestId('pin-dialog')
      await expect(pin.getByRole('heading', { name: 'PIN for Fixture Clean' })).toBeVisible()
      for (const digit of '2468') await pin.getByRole('button', { name: digit, exact: true }).click()
      await pin.getByTestId('pin-ok').click()
    }
    await expect(picker).toHaveAttribute('data-mandatory', 'true')
    await enterPin()
    await expect(receiver.getByTestId('home')).toBeVisible()
    await expect.poll(() => receiver.evaluate(() => localStorage.getItem(Object.keys(localStorage).find((k) => /^sh\.idle\.limit\.v1:/.test(k)) ?? ''))).toBe('1')
    // Advance the receiver clock past the configured window, then wake its real idle hook.
    await receiver.evaluate(() => {
      const key = Object.keys(localStorage).find((k) => /^sh\.idle\.v1:/.test(k))
      if (!key) throw new Error('The configured receiver timeout was not loaded')
      const actualNow = Date.now
      Date.now = () => actualNow() + 65_000
      window.dispatchEvent(new Event('pageshow'))
      window.dispatchEvent(new Event('sh:playback-activity'))
    })
    await expect(picker).toHaveAttribute('data-mandatory', 'true', { timeout: 12000 })
    await expect(receiver.getByTestId('home')).toHaveCount(0)
    expect(await receiver.evaluate(() => !!localStorage.getItem('sh.session.v1'))).toBe(true)
    const paired = await receiver.request.get('/api/v1/auth/me', { headers: { Authorization: 'Bearer ' + tokens.access_token } })
    expect(paired.status()).toBe(200)
    expect((await paired.json()).shared_device).toBe(true)
    // The idle lock did not bypass the existing profile PIN.
    await enterPin()
    await expect(receiver.getByTestId('home')).toBeVisible()
  } finally {
    await tv.close()
  }
})
