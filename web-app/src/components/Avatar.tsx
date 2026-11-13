import type { CSSProperties } from 'react'
import { SafeImg } from './SafeImg'
import { hueOf } from '../lib/util'

/** The pictures a managed user can be given. The server stores the name; each client draws it. */
export const AVATARS = ['fox', 'owl', 'bear', 'cat', 'dog', 'frog', 'panda', 'rabbit', 'lion', 'penguin', 'whale', 'turtle'] as const
const GLYPH: Record<string, string> = {
  fox: '🦊', owl: '🦉', bear: '🐻', cat: '🐱', dog: '🐶', frog: '🐸', panda: '🐼', rabbit: '🐰', lion: '🦁', penguin: '🐧', whale: '🐳', turtle: '🐢',
}

/** A person as a disc: their Google picture, else their chosen avatar, else their initial. `size` is in rem, or a CSS length (beside icons, which are sized in pixels). */
export function Avatar({ name, avatar, picture, size = 2.5 }: { name?: string | null; avatar?: string | null; picture?: string | null; size?: number | string }) {
  const glyph = avatar ? GLYPH[avatar] : undefined
  const hue = hueOf(avatar ?? '')
  const length = typeof size === 'number' ? `${size}rem` : size
  const disc: CSSProperties = {
    width: length, height: length, borderRadius: 999, flex: 'none', display: 'grid', placeItems: 'center', overflow: 'hidden', fontWeight: 700, lineHeight: 1,
    fontSize: `calc(${length} * ${glyph ? 0.56 : 0.5})`,
    ...(glyph ? { background: `linear-gradient(160deg, hsl(${hue} 40% 28%), hsl(${(hue + 50) % 360} 45% 16%))` } : { background: 'var(--t-accent)', color: 'var(--t-accent-text)' }),
  }
  const drawn = glyph ?? (name?.trim() || '?').slice(0, 1).toUpperCase()
  return (
    <span style={disc} aria-hidden="true" data-avatar={glyph ? avatar : undefined}>
      <SafeImg src={picture} alt="" referrerPolicy="no-referrer" style={{ width: '100%', height: '100%', objectFit: 'cover' }} fallback={drawn} />
    </span>
  )
}
