import { expect, test } from '@playwright/test'
import { signIn } from './helpers'

for (const scenario of ['preferred', 'unknown', 'foreign'] as const) {
  test(`runtime ${scenario} audio never stops playback`, async ({ page }) => {
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
    type Fixture = { runtimeAudio?: { requested: number | null; confirm(): void; change(): void } }
    // The fixture engine is attached (not a real stream that happens to play).
    await expect.poll(() => page.evaluate(() => !!(window as unknown as Fixture).runtimeAudio)).toBe(true)
    if (scenario === 'preferred') {
      // A tagged preferred track is switched to first, silent only while the switch lands.
      await expect.poll(() => page.evaluate(() => (window as unknown as Fixture).runtimeAudio!.requested)).toBe(1)
      await page.evaluate(() => (window as unknown as Fixture).runtimeAudio!.confirm())
    }
    // Unknown or foreign audio plays as it is: nothing stops for a confirmation.
    await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
    await expect(page.getByTestId('runtime-audio-blocked')).toHaveCount(0)
    expect(await page.getByTestId('video').evaluate((v: HTMLVideoElement) => v.muted)).toBe(false)
    // The stream changing its audio mid-play does not stop it either.
    await page.evaluate(() => (window as unknown as Fixture).runtimeAudio!.change())
    await page.waitForTimeout(300)
    await expect(page.getByTestId('player')).toHaveAttribute('data-state', 'playing')
    await expect(page.getByTestId('runtime-audio-blocked')).toHaveCount(0)
    await expect(page.getByTestId('player-menu')).toHaveCount(0)
  })
}
