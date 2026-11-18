// The title page's own glyphs, drawn like the shared Icon set (24-unit box, round 1.8 strokes). Kept
// here rather than in Icon so the page's look can change without touching every screen's icons.
const PATHS: Record<string, string> = {
  restart: 'M3 12a9 9 0 1 0 3-6.7M3 3v6h6',
  watched: 'M12 3a9 9 0 1 0 0 18 9 9 0 1 0 0-18zM8 12.5l2.7 2.7 5.8-5.7',
  list: 'M6 3.5h12v17l-6-4-6 4z',
  dots: 'M5 10.4a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 1 0 0-3.2zM12 10.4a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 1 0 0-3.2zM19 10.4a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 1 0 0-3.2z',
  people: 'M9 4.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 1 0 0-7zM2.5 20a6.5 6.5 0 0 1 13 0M17 6.2a2.8 2.8 0 1 0 0 5.6 2.8 2.8 0 1 0 0-5.6zM16 14.2a5.5 5.5 0 0 1 5.5 5.8',
  next: 'M5 5v14l10-7zM19 5v14',
  heart: 'M12 20s-7.5-4.6-7.5-10A4.3 4.3 0 0 1 12 7a4.3 4.3 0 0 1 7.5 3c0 5.4-7.5 10-7.5 10z',
  audio: 'M4 9.5h4l5-4v13l-5-4H4zM16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12',
  version: 'M3 4h18v12H3zM8 20h8M12 16v4',
  trailer: 'M3 4h18v16H3zM7 4v16M17 4v16M3 9h4M17 9h4M3 15h4M17 15h4',
  request: 'M12 5v14M5 12h14',
  play: 'M7 4.5v15l13-7.5z',
  chev: 'm9 6 6 6-6 6',
  check: 'm5 12.5 4.5 4.5L19 7.5',
}
const FILLED = new Set(['play', 'dots'])

export function TitleIcon({ name, size = 22, filled }: { name: keyof typeof PATHS; size?: number | string; filled?: boolean }) {
  const solid = filled ?? FILLED.has(name)
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={solid ? 'currentColor' : 'none'} stroke={name === 'dots' ? 'none' : 'currentColor'}
      strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name]} />
    </svg>
  )
}
