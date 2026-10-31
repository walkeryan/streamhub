import { lazy, Suspense, useEffect } from 'react'
import { Navigate, Outlet, Route, Routes, useLocation } from 'react-router'
import { useDisclaimer } from './api/hooks'
import { Rail } from './components/Rail'
import { Skeleton, Toasts } from './components/ui'
import { FocusGroup } from './nav/Focusable'
import { useGlobalBack } from './nav/back'
import { bootTheme, useTheme } from './lib/theme'
import { useSession } from './store/session'
import { DisclaimerGate, ProfilePicker } from './screens/Gate'
import { Home } from './screens/Home'
import { Search } from './screens/Search'
import { TitleScreen } from './screens/Title'
import { Browse } from './screens/Browse'
import { LiveTV } from './screens/LiveTV'
import { Favorites } from './screens/Favorites'
import { Sources } from './screens/Sources'
import { Pair } from './screens/Pair'

const Player = lazy(() => import('./screens/Player').then((m) => ({ default: m.Player })))
const Guide = lazy(() => import('./screens/Guide').then((m) => ({ default: m.Guide })))
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
  useTheme(profile?.theme_id)
  useGlobalBack()

  let body
  if (!profile) body = <ProfilePicker />
  else if (disclaimer.data && profile.disclaimer_accepted_version !== disclaimer.data.version) {
    body = <DisclaimerGate onAccepted={() => setProfile({ ...profile, disclaimer_accepted_version: disclaimer.data!.version })} />
  } else if (disclaimer.isLoading) body = <div className="page-pad"><Skeleton style={{ height: '20rem' }} /></div>
  else {
    body = (
      <Suspense fallback={null}>
        <Routes>
          <Route path="/play/:id" element={<Player />} />
          <Route element={<Shell />}>
            <Route index element={<Home />} />
            <Route path="search" element={<Search />} />
            <Route path="title/:id" element={<TitleScreen />} />
            <Route path="browse/:section" element={<Browse />} />
            <Route path="live" element={<LiveTV />} />
            <Route path="guide" element={<Guide />} />
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
