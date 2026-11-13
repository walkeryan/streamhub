import { useState } from 'react'
import { useLocation } from 'react-router'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { Icon } from './Icon'
import { Modal } from './ui'
import { useGo, useBackHandler } from '../nav/back'
import { setFocus } from '../nav/spatial'
import { can, type Capability } from '../lib/can'
import { useAccess, useMe } from '../api/me'
import { useSwitchable } from '../api/people'
import { useWho } from '../store/who'
import { usePhone } from '../lib/layout'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'
import { JoinRoomDialog } from '../room/JoinRoomDialog'
import { useRecordings } from '../api/hooks'

interface Destination { to: string; label: string; icon: string; /** path prefixes that also count as being here */ at?: string[]; needs?: Capability }

const HOME: Destination = { to: '/', label: 'Home', icon: 'home' }
const SEARCH: Destination = { to: '/search', label: 'Search', icon: 'search' }
const LIVE: Destination = { to: '/live', label: 'Live TV', icon: 'tv', at: ['/guide'] }
const MY_LIST: Destination = { to: '/favorites', label: 'My List', icon: 'bookmark' }
/** Wide screens: every place to watch has its own entry, each with its own icon. */
const NAV: Destination[] = [
  HOME, SEARCH, LIVE,
  { to: '/browse/movies', label: 'Movies', icon: 'film', at: ['/browse/vod', '/discover/movies'] },
  { to: '/browse/shows', label: 'Shows', icon: 'shows', at: ['/browse/series', '/discover/shows'] },
  MY_LIST,
]
/** A phone's bar holds five: Movies and Shows share Library (the screen has the switch), My List moves under More. */
const BAR: Destination[] = [HOME, SEARCH, LIVE, { to: '/browse/movies', label: 'Library', icon: 'film', at: ['/browse', '/discover'] }]
/** Everything else, each offered only to a user who may use it. */
const MORE: Destination[] = [
  { to: '/sports', label: 'Sports', icon: 'trophy', needs: 'sports' },
  { to: '/requests', label: 'Requests', icon: 'list', needs: 'requests' },
  { to: '/recordings', label: 'Recordings', icon: 'record', needs: 'recordings' },
  { to: '/sources', label: 'Sources', icon: 'source', needs: 'sources' },
  { to: '/users', label: 'Users', icon: 'users', needs: 'users' },
  { to: '/settings', label: 'Settings', icon: 'settings' },
]
const testId = (label: string) => `nav-${label.toLowerCase().replace(' ', '-')}`

export function Rail() {
  const loc = useLocation()
  const go = useGo()
  const phone = usePhone()
  const profile = useSession((s) => s.profile)
  const access = useAccess()
  const pinned = useUi((s) => s.railPinned)
  const setPinned = useUi((s) => s.setRailPinned)
  const [hasFocus, setHasFocus] = useState(false)
  const [menu, setMenu] = useState(false)
  const [joinRoom, setJoinRoom] = useState(false)
  const expanded = hasFocus || pinned
  // Back from the rail collapses it and returns focus to the screen content.
  useBackHandler(() => {
    if (!hasFocus && !pinned) return false
    setPinned(false)
    void setFocus('MAIN')
  }, hasFocus || pinned)
  const here = (d: Destination) => (d.to === '/' ? loc.pathname === '/' : [d.to, ...(d.at ?? [])].some((p) => loc.pathname.startsWith(p)))
  // Recordings: offered to who may use them once the server says recording is on. While it is off only
  // a holder of system.manage sees the entry, to learn why.
  const manages = can(access, 'settings.admin')
  const recordings = useRecordings(can(access, 'recordings') || manages, false)
  const recordingsOffered = !!recordings.data && (recordings.data.enabled ? can(access, 'recordings') : manages)
  const more = [...(phone ? [MY_LIST] : []), ...MORE.filter((d) => (d.to === '/recordings' ? recordingsOffered : !d.needs || can(access, d.needs)))]
  const open = (to: string) => { setPinned(false); setMenu(false); go(to) }
  // On a shared household device the profile entry asks who is watching; elsewhere it opens the profile's settings.
  const switchable = useSwitchable(!!useMe().data?.shared_device).data
  const showWho = useWho((s) => s.show)
  const openProfile = () => { if (switchable) { setPinned(false); setMenu(false); showWho() } else open('/settings?tab=profiles') }
  return <>
    <FocusGroup focusKey="RAIL" as="nav" label="Main navigation" className="rail" remember onFocusChange={setHasFocus}
      data-testid="rail" data={{ expanded }}>
      {(phone ? BAR : NAV).map((n) => (
        <Focusable key={n.label} focusKey={`rail:${n.to}`} className="rail-item fx-flat" role="link" label={n.label} title={n.label}
          aria-current={here(n) ? 'page' : undefined} data-testid={testId(n.label)} onEnter={() => open(n.to)}>
          <Icon name={n.icon} />
          <span className="lbl">{n.label}</span>
        </Focusable>
      ))}
      <Focusable focusKey="rail:more" className="rail-item fx-flat" label="More" title="More" aria-current={more.some(here) ? 'page' : undefined}
        data-testid="nav-more" onEnter={() => setMenu(true)}>
        <Icon name="menu" />
        <span className="lbl">More</span>
      </Focusable>
      {!phone && <>
        <div className="spacer" style={{ flex: 1 }} />
        <Focusable focusKey="rail:profile" className="rail-item fx-flat" role="link" label={`Profile ${profile?.name ?? ''}`} title={profile?.name}
          data-testid="nav-profile" onEnter={openProfile}>
          <Avatar name={profile?.name} />
          <span className="lbl">{profile?.name ?? 'Profile'}</span>
        </Focusable>
      </>}
    </FocusGroup>
    {/* outside the rail's focus group: an open menu must not count as focus in the rail */}
    <Modal open={menu} onClose={() => setMenu(false)} title="More" firstFocusKey={`more:${more[0]?.to}`} variant="sheet" testId="more-menu">
      <FocusGroup focusKey="more:list" className="flex flex-col gap-2">
        {more.map((d) => (
          <Focusable key={d.to} focusKey={`more:${d.to}`} className="menu-item fx-flat" role="link" label={d.label}
            aria-current={here(d) ? 'page' : undefined} data-testid={testId(d.label)} onEnter={() => open(d.to)}>
            <Icon name={d.icon} />{d.label}
          </Focusable>
        ))}
        {/* watch rooms */}
        {can(access, 'rooms.join') && <Focusable focusKey="more:join-room" className="menu-item fx-flat" label="Join a room" data-testid="nav-join-room" onEnter={() => { setPinned(false); setMenu(false); setJoinRoom(true) }}>
          <Icon name="people" />Join a room
        </Focusable>}
        {phone && <Focusable focusKey="more:profile" className="menu-item fx-flat" role="link" label={`Profile ${profile?.name ?? ''}`}
          data-testid="nav-profile" onEnter={openProfile}>
          <Avatar name={profile?.name} />{profile?.name ?? 'Profile'}
        </Focusable>}
      </FocusGroup>
    </Modal>
    <JoinRoomDialog open={joinRoom} onClose={() => setJoinRoom(false)} />
  </>
}

function Avatar({ name }: { name?: string }) {
  return (
    <span style={{ width: 22, height: 22, borderRadius: 999, background: 'var(--t-accent)', color: 'var(--t-accent-text)', display: 'grid', placeItems: 'center', fontSize: 12, fontWeight: 700, flex: 'none' }}>
      {(name ?? '?').slice(0, 1).toUpperCase()}
    </span>
  )
}
