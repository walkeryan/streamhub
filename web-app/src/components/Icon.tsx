const P: Record<string, string> = {
  home: 'M3 11l9-8 9 8M5 10v10h5v-6h4v6h5V10',
  search: 'M11 4a7 7 0 100 14 7 7 0 000-14zM21 21l-4.5-4.5',
  tv: 'M3 6h18v12H3zM8 21h8M12 18v3',
  guide: 'M3 5h18v14H3zM3 10h18M9 10v9M15 5v5',
  film: 'M4 4h16v16H4zM4 9h16M4 15h16M9 4v16M15 4v16',
  shows: 'M3 7h18v13H3zM8 3l4 4 4-4',
  heart: 'M12 20s-8-5-8-11a4.5 4.5 0 018-2.7A4.5 4.5 0 0120 9c0 6-8 11-8 11z',
  source: 'M4 5h16v6H4zM4 13h16v6H4zM8 8h.01M8 16h.01',
  settings: 'M12 9a3 3 0 100 6 3 3 0 000-6zM19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 01-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 010-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 014 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 010 4h-.1a1.7 1.7 0 00-1.5 1z',
  play: 'M7 4l13 8-13 8z',
  pause: 'M6 4h4v16H6zM14 4h4v16h-4z',
  plus: 'M12 5v14M5 12h14',
  check: 'M5 12l5 5 9-10',
  bookmark: 'M6 3h12v18l-6-4-6 4z',
  pip: 'M3 5h18v14H3zM12 12h7v5h-7z',
  menu: 'M4 6h16M4 12h16M4 18h16',
  back: 'M15 5l-7 7 7 7',
  x: 'M6 6l12 12M18 6L6 18',
  chevR: 'M9 5l7 7-7 7',
  chevD: 'M5 9l7 7 7-7',
  refresh: 'M20 11a8 8 0 10-2.3 5.7M20 4v7h-7',
  trash: 'M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13',
  user: 'M12 4a4 4 0 100 8 4 4 0 000-8zM4 21a8 8 0 0116 0',
  link: 'M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1M14 10a4 4 0 00-5.7 0l-3 3A4 4 0 0011 18.7l1-1',
  external: 'M14 4h6v6M20 4l-9 9M18 14v6H4V6h6',
  moon: 'M20 14A8 8 0 1110 4a6.5 6.5 0 0010 10z',
  cc: 'M3 6h18v12H3zM8 10a2 2 0 100 4M15 10a2 2 0 100 4',
  audio: 'M4 9v6h4l5 4V5L8 9zM16 9a4 4 0 010 6',
  aspect: 'M3 6h18v12H3zM7 10v4M17 10v4',
  fullscreen: 'M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5',
  list: 'M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01',
  up: 'M5 15l7-7 7 7',
  down: 'M5 9l7 7 7-7',
  star: 'M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z',
  tmdb: 'M4 8h16M12 8v10',
  info: 'M12 3a9 9 0 100 18 9 9 0 000-18zM12 11v6M12 7.5h.01',
  upload: 'M12 16V4M7 9l5-5 5 5M4 20h16',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 9a3 3 0 100 6 3 3 0 000-6z',
  tv2: 'M3 7h18v10H3z',
}
const FILLED = new Set(['play', 'pause'])

export function Icon({ name, size = 22, className, filled }: { name: keyof typeof P | string; size?: number; className?: string; filled?: boolean }) {
  const f = filled ?? FILLED.has(name)
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={f ? 'currentColor' : 'none'} stroke="currentColor"
      strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className}>
      <path d={P[name] ?? P.info} />
    </svg>
  )
}
