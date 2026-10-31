import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

async function failResolves(page: Page) {
  await page.addInitScript(() => {
    const original = window.fetch.bind(window)
    const state = window as unknown as { resolveIds: string[] }
    state.resolveIds = []
    window.fetch = async (input, init) => {
      const req = new Request(input, init)
      if (new URL(req.url).pathname === '/api/v1/playback/resolve') {
        state.resolveIds.push((await req.clone().json()).id)
        return Response.json({ code: 'provider_unavailable', message: 'Fixture provider unavailable' }, { status: 503 })
      }
      return original(req)
    }
  })
}
const requests = (page: Page) => page.evaluate(() => (window as unknown as { resolveIds: string[] }).resolveIds)
async function openChannel(page: Page) {
  await signIn(page)
  await page.getByRole('link', { name: 'Live TV', exact: true }).click()
  await page.getByTestId('channel-row').first().click()
}

test('fatal errors share one bounded retry timer and manual retry restarts the budget', async ({ page }) => {
  await failResolves(page)
  await page.clock.install()
  await openChannel(page)
  const player = page.getByTestId('player')
  await expect(player).toHaveAttribute('data-state', 'reconnecting')
  const initial = (await requests(page)).length
  await page.getByTestId('video').evaluate((video) => {
    for (let i = 0; i < 12; i++) video.dispatchEvent(new Event('error'))
  })
  for (let i = 0; i < 6; i++) {
    await page.clock.fastForward(Math.min(15000, 500 * 2 ** i) + 5)
    await expect.poll(async () => (await requests(page)).length).toBe(initial + i + 1)
  }
  await expect(player).toHaveAttribute('data-state', 'error')
  await page.clock.fastForward(60000)
  expect((await requests(page)).length).toBe(initial + 6)
  await page.getByRole('button', { name: 'Try again', exact: true }).click()
  await expect.poll(async () => (await requests(page)).length).toBe(initial + 7)
  await expect(player).toHaveAttribute('data-state', 'reconnecting')
})

test('switching channel and leaving player cancel the previous pending retry', async ({ page }) => {
  await failResolves(page)
  await page.clock.install()
  await openChannel(page)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'reconnecting')
  const before = await requests(page)
  const originalId = before[0]
  await page.keyboard.press('ArrowUp')
  await expect.poll(async () => (await requests(page)).at(-1)).not.toBe(originalId)
  const oldCount = (await requests(page)).filter((id) => id === originalId).length
  await page.clock.fastForward(510)
  expect((await requests(page)).filter((id) => id === originalId).length).toBe(oldCount)
  await page.keyboard.press('ArrowRight')
  await page.getByRole('button', { name: 'Back', exact: true }).click()
  await expect(page.getByTestId('live')).toBeVisible()
  const stopped = (await requests(page)).length
  await page.clock.fastForward(60000)
  expect((await requests(page)).length).toBe(stopped)
})

test('browser autoplay rejection becomes paused without reconnecting', async ({ page }) => {
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = () => Promise.reject(new DOMException('Fixture autoplay blocked', 'NotAllowedError'))
  })
  await openChannel(page)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'paused')
  await expect(page.getByTestId('player-error')).toHaveCount(0)
})

test('a stream that never produces media leaves starting state after the startup timeout', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(HTMLMediaElement.prototype, 'src', { configurable: true, get: () => '', set: () => undefined })
    HTMLMediaElement.prototype.play = () => Promise.resolve()
  })
  await page.clock.install()
  await openChannel(page)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'loading')
  await page.clock.fastForward(25010)
  await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'reconnecting')
})
