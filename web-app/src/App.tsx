import { lazy, Suspense, useEffect } from 'react'
import { Navigate, Outlet, Route, Routes, useLocation } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from './api/client'
import type { Profile } from './api/types'
import { useDisclaimer } from './api/hooks'
import { Rail } from './components/Rail'
import { ErrorState, Skeleton, Toasts } from './components/ui'
import { FocusGroup } from './nav/Focusable'
import { useGlobalBack } from './nav/back'
import { bootTheme, useTheme } from './lib/theme'
import { useSession } from './store/session'
import { DisclaimerGate } from './screens/Gate'
import { LoginGate } from './screens/GoogleLogin'
import { Home } from './screens/Home'
import { Search } from './screens/Search'
import { TitleScreen } from './screens/Title'
import { Browse } from './screens/Browse'
import { LiveTV } from './screens/LiveTV'
import { Favorites } from './screens/Favorites'
import { Sources } from './screens/Sources'
import { Pair } from './screens/Pair'

const Player = lazy(() => import('./screens/Player').then((m) => ({ default: m.Player })))
const Sports = lazy(() => import('./screens/Sports').then((m) => ({ default: m.Sports })))
const Settings = lazy(() => import('./screens/Settings').then((m) => ({ default: m.Settings })))

bootTheme()

function Shell() {
  const loc = useLocation()
  useEffect(() => { window.scrollTo(0, 0) }, [loc.pathname])
  return (
    <div className="shell">
      <Rail />
      <FocusGroup focusKey="MAIN" as="main" className="main" remember={false}>
        <Suspense fallback={<div className="page-pad"><Skeleton style={{ height: '16rem' }} /></div>}><Outlet /></Suspense>
      </FocusGroup>
    </div>
  )
}

export function App() {
  const profile = useSession((s) => s.profile)
  const setProfile = useSession((s) => s.setProfile)
  const disclaimer = useDisclaimer()
  const googleComplete = new URLSearchParams(window.location.search).has('google_complete')
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
  useGlobalBack()

  let body
  if (!profile || googleComplete) body = <LoginGate />
  else if (current.isPending) body = <div className="page-pad"><Skeleton style={{ height: '20rem' }} /></div>
  else if (current.error) body = <div className="page-pad"><ErrorState error={current.error} onRetry={() => void current.refetch()} /></div>
  else if (disclaimer.data && profile.disclaimer_accepted_version !== disclaimer.data.version) {
    body = <DisclaimerGate onAccepted={() => setProfile({ ...profile, disclaimer_accepted_version: disclaimer.data!.version })} />
  } else if (disclaimer.isLoading) body = <div className="page-pad"><Skeleton style={{ height: '20rem' }} /></div>
  else {
    body = (
      <Suspense fallback={null}>
        <Routes key={profile.id}>
          <Route path="/play/:id" element={<Player />} />
          <Route element={<Shell />}>
            <Route index element={<Home />} />
            <Route path="search" element={<Search />} />
            <Route path="title/:id" element={<TitleScreen />} />
            <Route path="browse/:section" element={<Browse />} />
            <Route path="live" element={<LiveTV />} />
            <Route path="guide" element={<Navigate to="/live?view=guide" replace />} />
            <Route path="sports" element={<Sports />} />
            <Route path="favorites" element={<Favorites />} />
            <Route path="sources" element={<Sources />} />
            <Route path="settings" element={<Settings />} />
            <Route path="pair" element={<Pair />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </Suspense>
    )
  }
  return <>{body}<Toasts /></>
}
