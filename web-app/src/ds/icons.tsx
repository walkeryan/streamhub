/*
 * The app's one icon set: every glyph on a 24-unit grid with round 1.9 strokes, the line style of the
 * approved title page and player. Merged from the shared set, the title page's and the player's (2026-11-18
 * restyle). Filled glyphs are the few that read better solid (play, pause, the ⋯ dots, the record dot).
 * The "10" and "30" inside the skip arrows are drawn as finer strokes (DIGITS), not text, so they never
 * join the words of the element around the icon.
 *
 * Add a glyph here rather than drawing one in a screen; never use an emoji as an icon.
 */
const P = {
  // places
  home: 'M3 11l9-8 9 8M5 10v10h5v-6h4v6h5V10',
  search: 'M11 4a7 7 0 100 14 7 7 0 000-14zM21 21l-4.5-4.5',
  tv: 'M3 6h18v12H3zM8 21h8M12 18v3',
  tv2: 'M3 7h18v10H3z',
  guide: 'M3 5h18v14H3zM3 10h18M9 10v9M15 5v5',
  film: 'M4 4h16v16H4zM4 9h16M4 15h16M9 4v16M15 4v16',
  shows: 'M3 7h18v13H3zM8 3l4 4 4-4',
  trophy: 'M7 4h10v5a5 5 0 01-10 0zM7 6H4v1a3 3 0 003 3M17 6h3v1a3 3 0 01-3 3M12 14v4M8 20h8',
  source: 'M4 5h16v6H4zM4 13h16v6H4zM8 8h.01M8 16h.01',
  settings: 'M12 9a3 3 0 100 6 3 3 0 000-6zM19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 01-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 010-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 014 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 010 4h-.1a1.7 1.7 0 00-1.5 1z',
  users: 'M9 5a3.5 3.5 0 100 7 3.5 3.5 0 000-7zM2.5 20a6.5 6.5 0 0113 0M16 5.2a3.5 3.5 0 010 6.6M18 14.3a6.5 6.5 0 013.5 5.7',
  user: 'M12 4a4 4 0 100 8 4 4 0 000-8zM4 21a8 8 0 0116 0',
  people: 'M9 4.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 1 0 0-7zM2.5 20a6.5 6.5 0 0 1 13 0M17 6.2a2.8 2.8 0 1 0 0 5.6 2.8 2.8 0 1 0 0-5.6zM16 14.2a5.5 5.5 0 0 1 5.5 5.8',
  record: 'M12 6a6 6 0 100 12 6 6 0 000-12z',
  // title actions
  play: 'M7 4.5v15l13-7.5z',
  pause: 'M7 4.5h4v15H7zM13 4.5h4v15h-4z',
  restart: 'M3 12a9 9 0 1 0 3-6.7M3 3v6h6',
  watched: 'M12 3a9 9 0 1 0 0 18 9 9 0 1 0 0-18zM8 12.5l2.7 2.7 5.8-5.7',
  bookmark: 'M6 3.5h12v17l-6-4-6 4z',
  thumbUp: 'M8 10h-5v11h5z M8 10l5-8c2 0 3 1.5 2 5l-1 3h5a2 2 0 0 1 2 2.4l-1.5 7a2 2 0 0 1-2 1.6H8z',
  thumbDown: 'M8 14h-5V3h5z M8 14l5 8c2 0 3-1.5 2-5l-1-3h5a2 2 0 0 0 2-2.4l-1.5-7a2 2 0 0 0-2-1.6H8z',
  heart: 'M12 20s-7.5-4.6-7.5-10A4.3 4.3 0 0 1 12 7a4.3 4.3 0 0 1 7.5 3c0 5.4-7.5 10-7.5 10z',
  dots: 'M5 10.4a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 1 0 0-3.2zM12 10.4a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 1 0 0-3.2zM19 10.4a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 1 0 0-3.2z',
  version: 'M3 4h18v12H3zM8 20h8M12 16v4',
  trailer: 'M3 4h18v16H3zM7 4v16M17 4v16M3 9h4M17 9h4M3 15h4M17 15h4',
  plus: 'M12 5v14M5 12h14',
  check: 'm5 12.5 4.5 4.5L19 7.5',
  star: 'M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z',
  info: 'M12 3a9 9 0 100 18 9 9 0 000-18zM12 11v6M12 7.5h.01',
  alert: 'M12 3.5l9.5 16.5h-19zM12 10v4.5M12 17.5h.01',
  // player
  next: 'M5 5v14l10-7zM19 5v14',
  prev: 'M19 5v14L9 12zM5 5v14',
  back10: 'M4 12a8 8 0 102.4-5.7M4 4v5h5',
  fwd10: 'M20 12a8 8 0 11-2.4-5.7M20 4v5h-5',
  fwd30: 'M20 12a8 8 0 11-2.4-5.7M20 4v5h-5',
  audio: 'M4 9.5h4l5-4v13l-5-4H4zM16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12',
  volume: 'M4 9.5h4l5-4v13l-5-4H4zM16.5 8.5a5 5 0 010 7',
  mute: 'M4 9.5h4l5-4v13l-5-4H4zM16.5 9.5l5 5M21.5 9.5l-5 5',
  cc: 'M5.5 5h13A2.5 2.5 0 0121 7.5v9a2.5 2.5 0 01-2.5 2.5h-13A2.5 2.5 0 013 16.5v-9A2.5 2.5 0 015.5 5zM10.5 10.2a2.3 2.3 0 100 3.6M17 10.2a2.3 2.3 0 100 3.6',
  expand: 'M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7',
  minimise: 'M4 14h6v6M20 10h-6V4M14 10l7-7M10 14l-7 7',
  fullscreen: 'M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5',
  pip: 'M3 5h18v14H3zM12 12h7v5h-7z',
  episodes: 'M4 6h12M4 12h12M4 18h8M18 15v6M15 18h6',
  tracks: 'M4 10v4M8 6v12M12 9v6M16 4v16M20 10v4',
  aspect: 'M3 6h18v12H3zM7 10v4M17 10v4',
  // general
  menu: 'M4 6h16M4 12h16M4 18h16',
  back: 'M15 5l-7 7 7 7',
  x: 'M6 6l12 12M18 6L6 18',
  mic: 'M12 3a3 3 0 00-3 3v6a3 3 0 006 0V6a3 3 0 00-3-3zM5.5 11a6.5 6.5 0 0013 0M12 17.5V21M8.5 21h7',
  chevR: 'm9 6 6 6-6 6',
  chevL: 'm15 6-6 6 6 6',
  chevD: 'M5 9l7 7 7-7',
  up: 'M5 15l7-7 7 7',
  down: 'M5 9l7 7 7-7',
  refresh: 'M20 11a8 8 0 10-2.3 5.7M20 4v7h-7',
  trash: 'M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13',
  delete: 'M21 5H9l-6 7 6 7h12zM12 9.5l5 5M17 9.5l-5 5',
  link: 'M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1M14 10a4 4 0 00-5.7 0l-3 3A4 4 0 0011 18.7l1-1',
  external: 'M14 4h6v6M20 4l-9 9M18 14v6H4V6h6',
  copy: 'M9 9h11v11H9zM5 15V4h11',
  list: 'M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01',
  filter: 'M4 6h16M7 12h10M10 18h4',
  lock: 'M6 11h12v9H6zM9 11V8a3 3 0 016 0v3',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 9a3 3 0 100 6 3 3 0 000-6z',
  moon: 'M20 14A8 8 0 1110 4a6.5 6.5 0 0010 10z',
  upload: 'M12 16V4M7 9l5-5 5 5M4 20h16',
  download: 'M12 4v12M7 11l5 5 5-5M4 20h16',
  exit: 'M10 4H5v16h5M15 8l4 4-4 4M19 12H9',
  tmdb: 'M4 8h16M12 8v10',
} as const

/** Names kept from the sets this one replaced, so either spelling draws the same glyph. */
const ALIASES = { gear: 'settings', chev: 'chevR', skip: 'next', request: 'plus' } as const

export type IconName = keyof typeof P | keyof typeof ALIASES
export const ICON_NAMES = [...Object.keys(P), ...Object.keys(ALIASES)] as IconName[]

const FILLED = new Set<string>(['play', 'pause', 'record', 'dots'])
const TEN = 'M9.9 10.9v4.6M12.7 12.1a1.15 1.15 0 012.3 0v2.2a1.15 1.15 0 01-2.3 0z'
const THIRTY = 'M8.8 10.9h2.1l-1.2 1.5a1.25 1.25 0 11-.9 2.25M13 12.1a1.15 1.15 0 012.3 0v2.2a1.15 1.15 0 01-2.3 0z'
const DIGITS: Partial<Record<string, string>> = { back10: TEN, fwd10: TEN, fwd30: THIRTY }

/**
 * One glyph. `size` is in px or any CSS length; the screens' CSS usually sizes icons in rem through
 * their container (`.x svg { width: …; height: … }`). Unknown names draw the info glyph.
 */
export function Icon({ name, size = 22, className, filled }: { name: IconName | string; size?: number | string; className?: string; filled?: boolean }) {
  const key = (ALIASES as Record<string, string>)[name] ?? name
  const d = (P as Record<string, string>)[key] ?? P.info
  const solid = filled ?? FILLED.has(key)
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" className={className}
      fill={solid ? 'currentColor' : 'none'} stroke={key === 'dots' || key === 'pause' ? 'none' : 'currentColor'}
      strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
      {DIGITS[key] && <path d={DIGITS[key]} fill="none" strokeWidth={1.4} />}
    </svg>
  )
}
