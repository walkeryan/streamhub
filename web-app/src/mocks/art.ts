// Procedural artwork for mock mode: no network, no bundled third-party images.
const enc = (svg: string) => `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`

function rng(seed: number) {
  let s = seed >>> 0
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296)
}
const hash = (t: string) => [...t].reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7)

function shapes(r: () => number, w: number, h: number, hue: number) {
  let out = ''
  for (let i = 0; i < 6; i++) {
    const cx = r() * w, cy = r() * h, rad = (0.15 + r() * 0.45) * Math.min(w, h) * (w > h ? 1.4 : 1)
    const hh = (hue + (r() - 0.5) * 70 + 360) % 360
    out += `<circle cx="${cx.toFixed(0)}" cy="${cy.toFixed(0)}" r="${rad.toFixed(0)}" fill="hsl(${hh.toFixed(0)} ${40 + r() * 30}% ${25 + r() * 25}%)" opacity="${0.35 + r() * 0.4}"/>`
  }
  return out
}

export function poster(title: string, year?: number): string {
  const seed = hash(title), r = rng(seed), hue = seed % 360
  const words = title.split(' ')
  const lines: string[] = []
  let cur = ''
  for (const w of words) {
    if ((cur + ' ' + w).trim().length > 11 && cur) { lines.push(cur); cur = w } else cur = (cur + ' ' + w).trim()
  }
  if (cur) lines.push(cur)
  const text = lines.slice(0, 4).map((l, i) => `<text x="24" y="${300 + i * 40}" font-family="Inter,Arial,sans-serif" font-weight="800" font-size="34" fill="#fff" letter-spacing="-1">${l.replace(/&/g, '&amp;')}</text>`).join('')
  return enc(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 450"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl(${hue} 55% 30%)"/><stop offset="1" stop-color="hsl(${(hue + 50) % 360} 60% 12%)"/></linearGradient></defs><rect width="300" height="450" fill="url(#g)"/>${shapes(r, 300, 450, hue)}<rect y="250" width="300" height="200" fill="#000" opacity=".35"/>${text}${year ? `<text x="24" y="430" font-family="Inter,Arial" font-size="16" fill="#fff" opacity=".7">${year}</text>` : ''}</svg>`)
}

export function backdrop(title: string): string {
  const seed = hash(title) + 99, r = rng(seed), hue = (seed * 7) % 360
  return enc(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" preserveAspectRatio="xMidYMid slice"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl(${hue} 50% 24%)"/><stop offset="1" stop-color="hsl(${(hue + 60) % 360} 55% 10%)"/></linearGradient></defs><rect width="1280" height="720" fill="url(#g)"/>${shapes(r, 1280, 720, hue)}</svg>`)
}

export function logo(name: string): string {
  const hue = hash(name) % 360
  return enc(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 60"><text x="60" y="38" text-anchor="middle" font-family="Inter,Arial" font-weight="800" font-size="22" fill="hsl(${hue} 80% 85%)">${name.replace(/&/g, '&amp;').slice(0, 7)}</text></svg>`)
}
