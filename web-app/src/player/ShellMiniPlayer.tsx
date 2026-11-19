import { useEffect, useState } from 'react'
import { matchPath, useLocation } from 'react-router'
import { useDock } from './dock'
import { MiniBar } from './MiniBar'
import { sendKeepClear, sendMiniPlayerCommand } from '../shell'
import { scrollClearOf, type CssRect } from '../shellProtocol'
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
  usePipClearance(shown ? state : null)
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

const rectOf = (el: Element): CssRect => { const r = el.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height } }
/** The element the remote is on: the spatial focus marker, else the DOM's focused element. */
const focusedElement = (): Element | null =>
  document.querySelector('[data-focused="true"]') ?? (document.activeElement && document.activeElement !== document.body ? document.activeElement : null)

/**
 * While the TV's player is in picture-in-picture: keep its window off what matters on this page.
 * A TV that takes keep-clear areas (Android 13+) is told where the focused item and the mini bar are,
 * and moves its window aside. One that cannot gets the fallback: the page scrolls the focused item
 * out from under the window it reported.
 */
function usePipClearance(state: { keep_clear: boolean; pip_rect: CssRect | null } | null) {
  const keepClear = !!state?.keep_clear
  const pip = state?.pip_rect ?? null
  const pipKey = pip ? `${pip.x},${pip.y},${pip.w},${pip.h}` : ''
  useEffect(() => {
    if (!state) return
    let timer = 0
    const report = () => {
      timer = 0
      const focused = focusedElement()
      const bar = document.querySelector('[data-testid="shell-mini-player"]')
      if (keepClear) {
        const rects = [focused, bar].filter((el): el is Element => !!el).map(rectOf).filter((r) => r.w > 0 && r.h > 0)
        sendKeepClear(rects, window.innerWidth)
      } else if (pip && focused && focused !== bar && !bar?.contains(focused)) {
        const dy = scrollClearOf(rectOf(focused), pip, window.innerHeight)
        if (dy !== 0) window.scrollBy({ top: dy, behavior: 'smooth' })
      }
    }
    const schedule = () => { if (!timer) timer = window.setTimeout(report, 150) }
    schedule()
    document.addEventListener('focusin', schedule)
    document.addEventListener('keyup', schedule)
    window.addEventListener('scroll', schedule, { passive: true })
    window.addEventListener('resize', schedule)
    return () => {
      if (timer) window.clearTimeout(timer)
      document.removeEventListener('focusin', schedule)
      document.removeEventListener('keyup', schedule)
      window.removeEventListener('scroll', schedule)
      window.removeEventListener('resize', schedule)
      if (keepClear) sendKeepClear([], window.innerWidth)
    }
    // pipKey stands for pip: a new object with the same rect should not restart the listeners.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [!!state, keepClear, pipKey])
}
