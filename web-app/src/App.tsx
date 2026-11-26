import './components/viewingAppearance.css'
import { lazy, Suspense, useEffect, useState } from 'react'
import { matchPath, Navigate, Outlet, Route, Routes, useLocation } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from './api/client'
import type { Profile } from './api/types'
import { useDisclaimer } from './api/hooks'
import { useMe } from './api/me'
import { useSwitchable } from './api/people'
import { useCatalogFreshness } from './api/catalogFreshness'
import { Rail } from './components/Rail'
import { WhoIsWatching } from './components/WhoIsWatching'
import { Button, ErrorState, Skeleton, Toasts } from './components/ui'
import { ControllerHint } from './components/PadHints'
import { FocusGroup } from './nav/Focusable'
import { useGlobalBack } from './nav/back'
import { useViewingAppearance } from './api/viewingPreferences'
import { bootTheme, useTheme } from './lib/theme'
import { useSession } from './store/session'
import { useReturnToRoom } from './room/returnToRoom'
import { useWho } from './store/who'
import { AwayFromHome, DisclaimerGate } from './screens/Gate'
import { LoginGate } from './screens/GoogleLogin'
import { InviteLanding } from './screens/Invite'
import { Home } from './screens/Home'
import { Search } from './screens/Search'
import { SearchCollection } from './screens/SearchCollection'
import { TitleScreen } from './screens/Title'
import { Discover } from './screens/Discover'
import { Browse } from './screens/Browse'
import { LiveTV } from './screens/LiveTV'
import { Favorites } from './screens/Favorites'
import { Requests } from './screens/Requests'
import { Sources } from './screens/Sources'
import { Pair } from './screens/Pair'
import { ShellPlay } from './screens/ShellPlay'
import { inShell } from './shell'
import { ShortcutHelp } from './player/ShortcutHelp'
import { PlayerHost } from './player/PlayerHost'

const Sports = lazy(() => import('./screens/Sports').then((m) => ({ default: m.Sports })))
const Settings = lazy(() => import('./screens/Settings').then((m) => ({ default: m.Settings })))
// watch rooms and recordings
const RoomScreen = lazy(() => import('./screens/Room').then((m) => ({ default: m.RoomScreen })))
const Recordings = lazy(() => import('./screens/Recordings').then((m) => ({ default: m.Recordings })))
const Users = lazy(() => import('./screens/Users').then((m) => ({ default: m.Users })))
const UserDetail = lazy(() => import('./screens/UserDetail').then((m) => ({ default: m.UserDetail })))

bootTheme()

/** html[data-scrolled] while the page is scrolled: the top bar is clear over the top of a page and frosts once content passes under it. */
function useScrolledFlag() {
  useEffect(() => {
    const root = document.documentElement
    const update = () => root.toggleAttribute('data-scrolled', window.scrollY > 8)
    update()
    window.addEventListener('scroll', update, { passive: true })
    return () => { window.removeEventListener('scroll', update); root.removeAttribute('data-scrolled') }
  }, [])
}

function Shell() {
  const [shortcuts, setShortcuts] = useState(false)
  useEffect(() => {
    const showHelp = (event: KeyboardEvent) => {
      if (event.key !== '?' || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey ||
          document.querySelector('[role="dialog"], [role="menu"]') ||
          (event.target instanceof HTMLElement && (event.target.isContentEditable || event.target.closest('input, textarea, select')))) return
      event.preventDefault()
      setShortcuts(true)
    }
    window.addEventListener('keydown', showHelp)
    return () => window.removeEventListener('keydown', showHelp)
  }, [])
  const loc = useLocation()
  useEffect(() => { window.scrollTo(0, 0) }, [loc.pathname])
  useScrolledFlag()
  return (
    <div className="shell">
      <Rail />
      {/* Down from the top bar, and Back while in it, return to what was focused here. */}
      <FocusGroup focusKey="MAIN" as="main" className="main" label={loc.pathname === '/search' ? 'Search' : undefined}>
        <Suspense fallback={<div className="page-pad"><Skeleton style={{ height: '16rem' }} /></div>}><Outlet context={{ openShortcuts: () => setShortcuts(true) }} /></Suspense>
      </FocusGroup>
      <ShortcutHelp open={shortcuts} onClose={() => setShortcuts(false)} />
    </div>
  )
}

export function App() {
  const profile = useSession((s) => s.profile)
  const setProfile = useSession((s) => s.setProfile)
  const signOut = useSession((s) => s.clear)
  const away = useSession((s) => s.away)
  const me = useMe()
  // A shared household device asks who is watching before it shows anything. No list (an older server,
  // or the server calls this a personal device) means no picker.
  const shared = !!profile && !!me.data?.shared_device
  const switchable = useSwitchable(shared)
  const who = useWho()
  const asks = shared && !who.picked && switchable.data !== null
  // "At the start" means just that: a device made shared while it is in use keeps going until its next start.
  const pickNow = who.pick
  const personal = me.data ? !me.data.shared_device : me.data === null
  useEffect(() => { if (personal) pickNow() }, [personal, pickNow])
  useCatalogFreshness(profile?.id)
  const disclaimer = useDisclaimer()
  const googleComplete = new URLSearchParams(window.location.search).has('google_complete')
  const invite = matchPath('/invite/:token', useLocation().pathname)?.params.token
  const current = useQuery({
    queryKey: ['current-profile', profile?.id],
    enabled: !!profile && !googleComplete,
    staleTime: 30_000,
    queryFn: async () => {
      const profiles = await unwrap<Profile[]>(api.GET('/profiles'))
      const found = profiles.find((p) => p.id === profile?.id)
      if (!found) throw new Error('Your profile is no longer available. Sign in again.')
      return found
    },
  })
  useEffect(() => {
    if (current.data && current.data.id === useSession.getState().profile?.id && JSON.stringify(current.data) !== JSON.stringify(useSession.getState().profile)) setProfile(current.data)
  }, [current.data, setProfile])
  useTheme(profile?.theme_id)
  useViewingAppearance()
  useGlobalBack()
  // A room link opened signed out returns to the room once signed in.
  useReturnToRoom(!!profile && !googleComplete)

  let body
  // An invitation is answered before anything else, whoever is or is not signed in.
  if (invite !== undefined && !googleComplete) body = <InviteLanding token={invite} />
  else if (!profile || googleComplete) body = <LoginGate />
  // Away from home without remote.access the server answers nothing else: say so in one place.
  else if (away) body = <AwayFromHome name={profile.name} onSignOut={signOut} />
  // Nothing is offered before the server has said what this user may do.
  else if (current.isPending || me.isPending) body = <div className="page-pad"><Skeleton style={{ height: '20rem' }} /></div>
  else if (current.error || me.error) body = (
    <div className="page-pad">
      <ErrorState error={current.error ?? me.error} onRetry={() => { void current.refetch(); void me.refetch() }}>
        <Button focusKey="boot:signout" onEnter={signOut}>Sign out</Button>
      </ErrorState>
    </div>
  )
  else if (asks && switchable.isPending) body = <div className="page-pad"><Skeleton style={{ height: '20rem' }} /></div>
  else if (asks) body = <WhoIsWatching mandatory users={switchable.data ?? undefined} error={switchable.error} onRetry={() => void switchable.refetch()} />
  else if (disclaimer.data && profile.disclaimer_accepted_version !== disclaimer.data.version) {
    body = <DisclaimerGate onAccepted={() => setProfile({ ...profile, disclaimer_accepted_version: disclaimer.data!.version })} />
  } else if (disclaimer.isLoading) body = <div className="page-pad"><Skeleton style={{ height: '20rem' }} /></div>
  else {
    body = (
      <Suspense fallback={null}>
        <Routes key={profile.id}>
          {/* The player itself lives above the routes (PlayerHost), so it can keep playing minimised.
              Inside the Android TV app the TV's own player plays (shell.ts). */}
          <Route path="/play/:id" element={inShell() ? <ShellPlay /> : null} />
          <Route path="/room/:code" element={<RoomScreen />} />
          <Route element={<Shell />}>
            <Route index element={<Home />} />
            <Route path="search" element={<Search />} />
            <Route path="search/actor/:id" element={<SearchCollection kind="actor" />} />
            <Route path="search/service/:id" element={<SearchCollection kind="service" />} />
            <Route path="title/:id" element={<TitleScreen />} />
            <Route path="discover/:section" element={<Discover />} />
            <Route path="browse/:section" element={<Browse />} />
            <Route path="live" element={<LiveTV />} />
            <Route path="guide" element={<Navigate to="/live?view=guide" replace />} />
            <Route path="sports" element={<Sports />} />
            <Route path="favorites" element={<Favorites />} />
            <Route path="requests" element={<Requests />} />
            <Route path="sources" element={<Sources />} />
            <Route path="users" element={<Users />} />
            <Route path="users/:id" element={<UserDetail />} />
            <Route path="settings" element={<Settings />} />
            <Route path="pair" element={<Pair />} />
            <Route path="recordings" element={<Recordings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
        <PlayerHost key={profile.id} />
        {who.open && switchable.data && <WhoIsWatching users={switchable.data} />}
      </Suspense>
    )
  }
  return <>{body}<Toasts /><ControllerHint /></>
}
