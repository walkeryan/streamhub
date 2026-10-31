/** Poster Adaptive theme: derive an accent from the focused artwork and write it to CSS variables. */
const canvas = typeof document !== 'undefined' ? document.createElement('canvas') : null

export async function dominantColor(src: string): Promise<[number, number, number] | null> {
  if (!canvas) return null
  try {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.src = src
    await img.decode()
    canvas.width = canvas.height = 24
    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    if (!ctx) return null
    ctx.drawImage(img, 0, 0, 24, 24)
    const d = ctx.getImageData(0, 0, 24, 24).data
    let best: [number, number, number] | null = null
    let bestScore = -1
    for (let i = 0; i < d.length; i += 4) {
      const r = d[i]!, g = d[i + 1]!, b = d[i + 2]!
      const max = Math.max(r, g, b), min = Math.min(r, g, b)
      const sat = max === 0 ? 0 : (max - min) / max
      const score = sat * (0.4 + max / 255)
      if (score > bestScore && max > 70) { bestScore = score; best = [r, g, b] }
    }
    return best
  } catch {
    return null
  }
}

function luminance([r, g, b]: [number, number, number]) {
  const f = (v: number) => { const c = v / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4 }
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}

export function applyAccent(rgb: [number, number, number] | null): void {
  const s = document.documentElement.style
  if (!rgb) { s.removeProperty('--t-accent'); s.removeProperty('--t-focus-glow'); s.removeProperty('--t-accent-text'); return }
  // lift dark colours so the accent stays legible on the dark surface
  const lift = luminance(rgb) < 0.18 ? 1.8 : 1
  const c = rgb.map((v) => Math.min(255, Math.round(v * lift))) as [number, number, number]
  const css = `rgb(${c[0]} ${c[1]} ${c[2]})`
  s.setProperty('--t-accent', css)
  s.setProperty('--t-focus-glow', css)
  // pick whichever text colour contrasts more with the accent (crossover is ~0.18, not 0.4; a 0.4 threshold put
  // white text on mid-luminance accents at 2.3–4.5:1, failing WCAG AA on primary buttons and active chips)
  const L = luminance(c)
  // (pure black rather than #0B0F17 keeps the worst case at ~4.6:1 instead of ~4.4:1)
  const onDark = (L + 0.05) / 0.05
  const onLight = 1.05 / (L + 0.05)
  s.setProperty('--t-accent-text', onDark >= onLight ? '#000000' : '#FFFFFF')
}
