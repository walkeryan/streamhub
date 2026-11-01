import { useLocation } from 'react-router'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { Icon } from './Icon'
import { useGo, useBackHandler } from '../nav/back'
import { setFocus } from '../nav/spatial'
import { useState } from 'react'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'

export const NAV = [
  { to: '/', label: 'Home', icon: 'home' },
  { to: '/search', label: 'Search', icon: 'search' },
  { to: '/live', label: 'Live TV', icon: 'tv' },
  { to: '/sports', label: 'Sports', icon: 'tv' },
  { to: '/discover/movies', label: 'Movies', icon: 'film' },
  { to: '/discover/shows', label: 'Series', icon: 'shows' },
  { to: '/favorites', label: 'Favorites', icon: 'heart' },
  { to: '/sources', label: 'Sources', icon: 'source' },
  { to: '/settings', label: 'Settings', icon: 'settings' },
] as const

export function Rail() {
  const loc = useLocation()
  const go = useGo()
  const profile = useSession((s) => s.profile)
  const pinned = useUi((s) => s.railPinned)
  const setPinned = useUi((s) => s.setRailPinned)
  const [hasFocus, setHasFocus] = useState(false)
  const expanded = hasFocus || pinned
  // Back from the rail collapses it and returns focus to the screen content.
  useBackHandler(() => {
    if (!hasFocus && !pinned) return false
    setPinned(false)
    void setFocus('MAIN')
  }, hasFocus || pinned)
  const active = (to: string) => (to === '/' ? loc.pathname === '/' : (loc.pathname.startsWith(to) || (to.startsWith('/discover/') && loc.pathname.startsWith(to.replace('/discover/', '/browse/')))))
  return (
    <FocusGroup focusKey="RAIL" as="nav" label="Main navigation" className="rail" remember onFocusChange={setHasFocus}
      data-testid="rail" data={{ expanded }}>
      {NAV.map((n) => (
        <Focusable key={n.to} focusKey={`rail:${n.to}`} className="rail-item fx-flat" role="link" label={n.label}
          aria-current={active(n.to) ? 'page' : undefined} data-testid={`nav-${n.label==='Series'?'shows':n.label.toLowerCase().replace(' ', '-')}`}
          onEnter={() => { setPinned(false); go(n.to) }}>
          <Icon name={n.icon} />
          <span className="lbl">{n.label}</span>
        </Focusable>
      ))}
      <div className="spacer" style={{ flex: 1 }} />
      <Focusable focusKey="rail:profile" className="rail-item fx-flat" role="link" label={`Profile ${profile?.name ?? ''}`}
        data-testid="nav-profile" onEnter={() => go('/settings?tab=profiles')}>
        <span style={{ width: 22, height: 22, borderRadius: 999, background: 'var(--t-accent)', color: 'var(--t-accent-text)', display: 'grid', placeItems: 'center', fontSize: 12, fontWeight: 700, flex: 'none' }}>
          {(profile?.name ?? '?').slice(0, 1).toUpperCase()}
        </span>
        <span className="lbl">{profile?.name ?? 'Profile'}</span>
      </Focusable>
    </FocusGroup>
  )
}
