import { lazy, Suspense, useEffect, useState } from 'react'
import { matchPath, useLocation } from 'react-router'
import { inShell } from '../shell'
import { isExpand, useDock } from './dock'
import { ShellMiniPlayer } from './ShellMiniPlayer'
import { useMiniPlayerSpace } from './useMiniPlayerSpace'
import { useSession } from '../store/session'

const Player = lazy(() => import('../screens/Player').then((m) => ({ default: m.Player })))

// A minimised player is the signed-in profile's: another profile, or signing out, ends it.
useSession.subscribe((next, previous) => {
  if (next.profile?.id !== previous.profile?.id || next.generation !== previous.generation) useDock.getState().close()
})

const decode = (raw: string | undefined): string => { try { return decodeURIComponent(raw ?? '') } catch { return '' } }

/**
 * The player above the router. A /play address shows it full screen; minimised, the same player (its
 * <video> and stream untouched) becomes the bar at the bottom while the routes below change.
 *
 * One player at a time. Entering /play from anywhere else starts a new one (replacing a mini player),
 * except the minimised player's own Expand. Within /play (zapping, the next episode) it stays the same.
 * Inside the Android TV app the TV plays; this shows the bar for the TV's picture-in-picture instead.
 */
export function PlayerHost() {
  const loc = useLocation()
  const mini = useDock((s) => s.mini)
  const close = useDock((s) => s.close)
  const match = matchPath('/play/:id', loc.pathname)
  const onPlay = !!match
  // Which player instance is current, worked out once per history entry (during render, so a new
  // title never renders first in the old player).
  const [track, setTrack] = useState({ key: '', onPlay: false, instance: 0 })
  let current = track
  if (track.key !== loc.key) {
    const fresh = onPlay && !track.onPlay && !(isExpand(loc.state) && mini)
    current = { key: loc.key, onPlay, instance: track.instance + (fresh ? 1 : 0) }
    setTrack(current)
  }
  const shell = inShell()
  // On a /play address the player is full screen: whatever it was minimised as is over.
  useEffect(() => { if (onPlay && mini && !shell) close() }, [onPlay, mini, close, shell])
  useMiniPlayerSpace(!shell && !onPlay && !!mini)
  if (shell) return <ShellMiniPlayer />
  if (match) {
    return <Suspense fallback={null}><Player key={current.instance} playId={decode(match.params.id)} playSearch={loc.search} /></Suspense>
  }
  if (mini) return <Suspense fallback={null}><Player key={current.instance} playId={mini.id} playSearch={mini.search} mini /></Suspense>
  return null
}
