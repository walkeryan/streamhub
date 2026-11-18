/*
 * The player's glyphs, drawn as in the approved player and mini player mockups (24-unit grid, 2-unit
 * round strokes). Kept apart from components/Icon.tsx: the player's set is its own, and the "10" and
 * "30" inside the skip arrows are text, which the shared icon paths cannot carry.
 */
const STROKES: Record<string, string> = {
  next: 'M5 5v14l10-7zM19 5v14',
  prev: 'M19 5v14L9 12zM5 5v14',
  audio: 'M4 9.5h4l5-4v13l-5-4H4zM16.5 8.5a5 5 0 010 7M19 6a8.5 8.5 0 010 12',
  cc: 'M5.5 5h13A2.5 2.5 0 0121 7.5v9a2.5 2.5 0 01-2.5 2.5h-13A2.5 2.5 0 013 16.5v-9A2.5 2.5 0 015.5 5zM10.5 10.2a2.3 2.3 0 100 3.6M17 10.2a2.3 2.3 0 100 3.6',
  gear: 'M12 9a3 3 0 100 6 3 3 0 000-6zM19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z',
  people: 'M9 4.5a3.5 3.5 0 100 7 3.5 3.5 0 000-7zM2.5 20a6.5 6.5 0 0113 0M17 6.2a2.8 2.8 0 100 5.6M16 14.2a5.5 5.5 0 015.5 5.8',
  expand: 'M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7',
  minimise: 'M4 14h6v6M20 10h-6V4M14 10l7-7M10 14l-7 7',
  x: 'M6 6l12 12M18 6L6 18',
  episodes: 'M4 6h12M4 12h12M4 18h8M18 15v6M15 18h6',
  fullscreen: 'M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5',
  volume: 'M4 9.5h4l5-4v13l-5-4H4zM16.5 8.5a5 5 0 010 7',
  mute: 'M4 9.5h4l5-4v13l-5-4H4zM16.5 9.5l5 5M21.5 9.5l-5 5',
  back10: 'M4 12a8 8 0 102.4-5.7M4 4v5h5',
  fwd30: 'M20 12a8 8 0 11-2.4-5.7M20 4v5h-5',
  skip: 'M5 5v14l10-7zM19 5v14',
}
const FILLS: Record<string, string> = {
  play: 'M7 4.5v15l13-7.5z',
  pause: 'M7 4.5h4v15H7zM13 4.5h4v15h-4z',
}
const LABELS: Record<string, string> = { back10: '10', fwd30: '30' }

export type PlayerIconName = keyof typeof STROKES | keyof typeof FILLS

export function PlayerIcon({ name, size = 24, className }: { name: PlayerIconName; size?: number; className?: string }) {
  const fill = FILLS[name]
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" className={className}
      fill={fill ? 'currentColor' : 'none'} stroke={fill ? 'none' : 'currentColor'} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <path d={fill ?? STROKES[name] ?? STROKES.x} />
      {LABELS[name] && <text x="12" y="15.5" fontSize="7" fontWeight="700" textAnchor="middle" fill="currentColor" stroke="none">{LABELS[name]}</text>}
    </svg>
  )
}
