import { useEffect, useState } from 'react'
import { matchPath, useLocation } from 'react-router'
import { useDock } from './dock'
import { MiniBar } from './MiniBar'
import { sendMiniPlayerCommand } from '../shell'
import { useMiniPlayerSpace } from './useMiniPlayerSpace'

/**
 * Inside the Android TV app: the TV's own player is in picture-in-picture, and this page shows the
 * same bottom bar a browser's mini player has, driving the TV's player over the shell bridge. The TV
 * says what plays (`mini_player` messages, held in useDock().shell); null hides the bar.
 */
export function ShellMiniPlayer() {
  const state = useDock((s) => s.shell)
  const since = useDock((s) => s.shellAt)
  const onPlay = !!matchPath('/play/:id', useLocation().pathname)
  // Between two of the TV's reports the time moves on by itself, so the bar does not tick in steps.
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!state?.playing || state.live) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [state])
  const shown = !!state && !onPlay
  useMiniPlayerSpace(shown)
  if (!state || !shown) return null
  const position = state.playing && !state.live ? Math.min(state.duration_ms ?? Infinity, state.position_ms + Math.max(0, now - since)) : state.position_ms
  return (
    <div className="mini-dock" data-testid="shell-mini-player">
      <MiniBar
        title={state.title} line={state.subtitle} positionMs={position} durationMs={state.duration_ms} playing={state.playing} live={state.live}
        hasPrevious={state.has_previous} hasNext={state.has_next} image={state.image}
        onPlayPause={() => sendMiniPlayerCommand('play_pause')}
        onSeekBy={(delta) => sendMiniPlayerCommand('seek_by', delta)}
        onPrevious={() => sendMiniPlayerCommand('previous')}
        onNext={() => sendMiniPlayerCommand('next')}
        onExpand={() => sendMiniPlayerCommand('expand')}
        onClose={() => { sendMiniPlayerCommand('close'); useDock.getState().setShell(null) }}
      />
    </div>
  )
}
