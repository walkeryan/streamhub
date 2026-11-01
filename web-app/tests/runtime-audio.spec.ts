import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

for (const scenario of ['preferred', 'unknown', 'foreign'] as const) {
  test(`runtime ${scenario} audio remains muted until confirmed or explicitly chosen`, async ({ page }) => {
    // Deterministic engine fixture exercises the real hook/UI, not provider media.
    await page.route('**/src/player/engine.ts', route => route.fulfill({ contentType: 'application/javascript', body: `
      export async function attachSource(video, descriptor, options) {
        let tracks = [{ id: 0, label: 'Default audio', language: ${scenario === 'unknown' ? 'undefined' : "'fra'"}, active: true }${scenario === 'preferred' ? ", { id: 1, label: 'English', language: 'eng', active: false }" : ''}];
        video.src = '/mock/sample.webm';
        window.runtimeAudio = { requested: null, confirm() { tracks = tracks.map(t => ({ ...t, active: t.id === this.requested })); options.onTracks(); }, change() { tracks = [{ id: 0, label: 'Changed audio', language: 'deu', active: true }]; options.onTracks(true); } };
        return { kind: 'hls', destroy() { video.removeAttribute('src'); video.load(); }, audioTracks: () => tracks, setAudio(id) { window.runtimeAudio.requested = id; }, subtitleTracks: () => [], setSubtitle() {} };
      }
    ` }))
    await signIn(page)
    await page.getByRole('link', { name: 'Live TV', exact: true }).click()
    await page.getByTestId('channel-row').first().click()
    await expect(page.getByTestId('runtime-audio-blocked')).toBeVisible()
    const mutedPaused = () => page.getByTestId('video').evaluate((v: HTMLVideoElement) => v.muted && v.paused)
    expect(await mutedPaused()).toBe(true)
    if (scenario === 'preferred') {
      expect(await page.evaluate(() => (window as unknown as { runtimeAudio: { requested: number | null; confirm(): void; change(): void } }).runtimeAudio.requested)).toBe(1)
      await page.evaluate(() => (window as unknown as { runtimeAudio: { requested: number | null; confirm(): void; change(): void } }).runtimeAudio.confirm())
    } else if (scenario === 'unknown') {
      await expect(page.getByRole('button', { name: 'Default audio · language unknown', exact: true })).toHaveAttribute('data-focused', 'true')
      await page.keyboard.press('Enter')
    } else {
      await page.getByRole('button', { name: 'Continue with current audio', exact: true }).click()
    }
    await expect(page.getByTestId('runtime-audio-blocked')).toHaveCount(0)
    await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
    await page.evaluate(() => (window as unknown as { runtimeAudio: { requested: number | null; confirm(): void; change(): void } }).runtimeAudio.change())
    await expect(page.getByTestId('runtime-audio-blocked')).toBeVisible()
    expect(await mutedPaused()).toBe(true)
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('player-menu')).toHaveCount(0)
    expect(await mutedPaused()).toBe(true)
  })
}
