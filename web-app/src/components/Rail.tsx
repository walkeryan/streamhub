import { useState } from 'react'
import { useLocation } from 'react-router'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { Avatar } from './Avatar'
import { Icon } from './Icon'
import { SearchBox } from './SearchBox'
import { Modal } from './ui'
import { useGo, useBackHandler } from '../nav/back'
import { setFocus } from '../nav/spatial'
import { can, type Capability } from '../lib/can'
import { useAccess, useMe } from '../api/me'
import { useSwitchable } from '../api/people'
import { useWho } from '../store/who'
import { usePhone } from '../lib/layout'
import { useSlashToSearch } from '../lib/searchQuery'
import { useSession } from '../store/session'
import { JoinRoomDialog } from '../room/JoinRoomDialog'
import { useRecordings } from '../api/hooks'

interface Destination { to: string; label: string; icon: string; /** path prefixes that also count as being here */ at?: string[]; needs?: Capability }

const HOME: Destination = { to: '/', label: 'Home', icon: 'home' }
const SEARCH: Destination = { to: '/search', label: 'Search', icon: 'search' }
const LIVE: Destination = { to: '/live', label: 'Live TV', icon: 'tv', at: ['/guide'] }
const MY_LIST: Destination = { to: '/favorites', label: 'My List', icon: 'bookmark' }
/** Wide screens: every place to watch is named in the top bar; the search box sits with More and the profile at its end. */
const NAV: Destination[] = [
  HOME, LIVE,
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

/** A phone has no box in its bar: "/" opens the search page, whose own box then takes the caret. */
function SlashOpensSearch() {
  const go = useGo()
  const at = useLocation().pathname
  useSlashToSearch(() => { if (at !== SEARCH.to) go(SEARCH.to) })
  return null
}

/** The one navigation: a bar across the top of a wide screen, a bar of five along the bottom of a phone. */
export function Rail() {
  const loc = useLocation()
  const go = useGo()
  const phone = usePhone()
  const profile = useSession((s) => s.profile)
  const access = useAccess()
  const [hasFocus, setHasFocus] = useState(false)
  const [menu, setMenu] = useState(false)
  const [joinRoom, setJoinRoom] = useState(false)
  // Back from the bar returns focus to where it was in the screen content.
  useBackHandler(() => { void setFocus('MAIN') }, hasFocus)
  const here = (d: Destination) => (d.to === '/' ? loc.pathname === '/' : [d.to, ...(d.at ?? [])].some((p) => loc.pathname.startsWith(p)))
  // Recordings: offered to who may use them once the server says recording is on. While it is off only
  // a holder of system.manage sees the entry, to learn why.
  const manages = can(access, 'settings.admin')
  const recordings = useRecordings(can(access, 'recordings') || manages, false)
  const recordingsOffered = !!recordings.data && (recordings.data.enabled ? can(access, 'recordings') : manages)
  const more = [...(phone ? [MY_LIST] : []), ...MORE.filter((d) => (d.to === '/recordings' ? recordingsOffered : !d.needs || can(access, d.needs)))]
  const open = (to: string) => { setMenu(false); go(to) }
  // Focus enters the bar on the entry for where the viewer is. A screen outside every section
  // (a title page) keeps the entry it was reached from.
  const at = (phone ? BAR : [...NAV, SEARCH]).find(here)?.to ?? (more.some(here) ? 'more' : undefined)
  const [entry, setEntry] = useState(at)
  if (at && at !== entry) setEntry(at)
  // On a shared household device the profile entry asks who is watching; elsewhere it opens the profile's settings.
  const me = useMe().data
  const switchable = useSwitchable(!!me?.shared_device).data
  // The person's Google picture, else the avatar chosen for them, else their initial: the size of the icons
  // beside it in a menu, a little larger at the end of the top bar.
  const face = (size: string) => <Avatar name={profile?.name} avatar={me?.user.avatar ?? profile?.avatar} picture={me?.user.picture} size={size} />
  const showWho = useWho((s) => s.show)
  const openProfile = () => { if (switchable) { setMenu(false); showWho() } else open('/settings?tab=profiles') }
  return <>
    <FocusGroup focusKey="RAIL" as="nav" label="Main navigation" className="rail" remember={false} preferred={entry && `rail:${entry}`}
      onFocusChange={setHasFocus} data-testid="rail">
      {!phone && <span className="rail-brand" onClick={() => open('/')}>StreamHub</span>}
      {/* A phone names each icon; the top bar has the room to say the name alone. */}
      {(phone ? BAR : NAV).map((n) => (
        <Focusable key={n.label} focusKey={`rail:${n.to}`} className="rail-item fx-flat" role="link" label={n.label}
          aria-current={here(n) ? 'page' : undefined} data-testid={testId(n.label)} onEnter={() => open(n.to)}>
          {phone && <Icon name={n.icon} />}
          <span className="lbl">{n.label}</span>
        </Focusable>
      ))}
      {/* Wide: the one search box, a stop of the bar like any other. A phone's Search tab opens the results page, which has the box. */}
      {!phone && <>
        <div className="rail-gap" />
        <SearchBox label={SEARCH.label} />
      </>}
      <Focusable focusKey="rail:more" className={phone ? 'rail-item fx-flat' : 'rail-item rail-icon fx-flat'} label="More" title="More"
        aria-current={more.some(here) ? 'page' : undefined} data-testid="nav-more" onEnter={() => setMenu(true)}>
        <Icon name="menu" />
        {phone && <span className="lbl">More</span>}
      </Focusable>
      {!phone && <Focusable focusKey="rail:profile" className="rail-item rail-icon fx-flat" role="link" label={`Profile ${profile?.name ?? ''}`} title={profile?.name}
        data-testid="nav-profile" onEnter={openProfile}>
        {face('1.75rem')}
        <span className="sr-only">{profile?.name}</span>
      </Focusable>}
    </FocusGroup>
    {/* outside the bar's focus group: an open menu must not count as focus in the bar */}
    <Modal open={menu} onClose={() => setMenu(false)} title="More" firstFocusKey={`more:${more[0]?.to}`} variant="sheet" testId="more-menu">
      <FocusGroup focusKey="more:list" className="flex flex-col gap-2">
        {more.map((d) => (
          <Focusable key={d.to} focusKey={`more:${d.to}`} className="menu-item fx-flat" role="link" label={d.label}
            aria-current={here(d) ? 'page' : undefined} data-testid={testId(d.label)} onEnter={() => open(d.to)}>
            <Icon name={d.icon} />{d.label}
          </Focusable>
        ))}
        {/* watch rooms */}
        {can(access, 'rooms.join') && <Focusable focusKey="more:join-room" className="menu-item fx-flat" label="Join a room" data-testid="nav-join-room" onEnter={() => { setMenu(false); setJoinRoom(true) }}>
          <Icon name="people" />Join a room
        </Focusable>}
        {phone && <Focusable focusKey="more:profile" className="menu-item fx-flat" role="link" label={`Profile ${profile?.name ?? ''}`}
          data-testid="nav-profile" onEnter={openProfile}>
          {face('22px')}{profile?.name ?? 'Profile'}
        </Focusable>}
      </FocusGroup>
    </Modal>
    <JoinRoomDialog open={joinRoom} onClose={() => setJoinRoom(false)} />
    {phone && <SlashOpensSearch />}
  </>
}
