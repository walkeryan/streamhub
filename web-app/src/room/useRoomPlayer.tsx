import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type ReactNode, type RefObject } from 'react'
import { useNavigate } from 'react-router'
import { Button, Modal } from '../components/ui'
import { Icon } from '../components/Icon'
import { FocusGroup } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import type { PlayState } from '../player/usePlayback'
import { seekPlan, seekableRanges } from '../player/restartSeek'
import { useUi } from '../store/ui'
import type { RoomSnapshot } from './RoomSocket.ts'
import { decide, isSeek, positionAt } from './roomSync.ts'
import type { RoomSession } from './session'
import { ShareSheet } from './ShareSheet'

/** What the room needs of the player it steers. */
export interface RoomPlayerLink {
  state: PlayState
  audioBlocked: boolean
  pause: () => void
  resume: () => void
  reload: () => void
  /** Seconds into the title at which the media element's time zero lies (restart-at-offset streams). */
  offsetS: number
  /** The title's length in seconds, 0 when unknown. */
  durationS: number
  /** The stream is produced by the server from a start position; outside what it has produced only a new stream helps. */
  restartable: boolean
}

const NO_ROOM: RoomSnapshot = { status: 'closed', members: [], state: null, waiting: null, controlsLocked: false, closed: null }
const noRoom = () => () => undefined
const SYNC_INTERVAL_MS = 500
/** A stall this short is a seek settling, not a member the room should wait for. */
const STALL_REPORT_MS = 500
/** A player still paused this long after the room started playing was not allowed to start by itself. */
const AUTOPLAY_GRACE_MS = 1500

const CLOSED: Record<string, { text: string; rejoin?: boolean }> = {
  expired: { text: 'Nobody was in the room for a while, so it closed.' },
  deleted: { text: 'The host closed the room.' },
  shutdown: { text: 'The server is restarting. Start or join a room again in a moment.' },
  silent: { text: 'Your connection was quiet for too long, so your seat was given up.', rejoin: true },
  replaced: { text: 'You joined this room again in another tab or on another device.', rejoin: true },
  slow: { text: 'Your connection could not keep up with the room.', rejoin: true },
  unauthorized: { text: 'Your seat in the room is no longer valid.', rejoin: true },
  not_a_member: { text: 'Your seat in the room was given up after a long disconnection.', rejoin: true },
  not_found: { text: 'This room no longer exists.' },
}

/**
 * Room mode for the player. The transport controls go to the room socket instead of the media element;
 * the room state comes back and steers the element (roomSync.decide). Also the room's own chrome:
 * who is in it, the wait for a buffering member, the host's lock, leaving, and the room ending.
 */
export function useRoomPlayer(room: RoomSession | undefined, videoRef: RefObject<HTMLVideoElement | null>, player: RoomPlayerLink, onRejoin?: () => void) {
  const navigate = useNavigate()
  const toast = useUi((s) => s.toast)
  const subscribe = useCallback((notify: () => void) => (room ? room.socket.subscribe(notify) : noRoom()), [room])
  const snapshot = useSyncExternalStore(subscribe, () => (room ? room.socket.snapshot : NO_ROOM))
  const [leaving, setLeaving] = useState(false)
  const [sharing, setSharing] = useState(false)
  const link = useRef(player)
  link.current = player
  const isHost = !!room?.room.is_host

  // One line of explanation at a time, however often a locked control is pressed.
  const lastNotice = useRef(0)
  const notice = useCallback((text: string, tone: 'info' | 'error' = 'info') => {
    if (Date.now() - lastNotice.current < 3000) return
    lastNotice.current = Date.now()
    toast(text, tone)
  }, [toast])
  useEffect(() => room?.onError((code, message) => notice(
    code === 'controls_locked' ? 'Only the host can control playback in this room.' : code === 'host_only' ? 'Only the host can do that.' : code === 'live_no_seek' ? 'A live channel cannot be moved.' : message || 'The room refused that.',
    code === 'bad_message' ? 'error' : 'info')), [room, notice])

  // ---- room state -> this member's player ----
  useEffect(() => {
    const video = videoRef.current
    if (!room || !video) return
    // One fresh descriptor per room state: a stream that still does not reach the room position will not after a second one.
    let refreshedFor: number | null = null
    // How long this player's seeks take: while the room plays, aim that far ahead so the seek lands on it.
    let lead = 0
    let seekStarted = 0
    let lastPlayTry = 0
    let stallTimer: number | null = null
    const report = (stalled: boolean) => {
      if (!stalled) {
        if (stallTimer !== null) { window.clearTimeout(stallTimer); stallTimer = null }
        room.socket.setBuffering(false)
      } else if (stallTimer === null) stallTimer = window.setTimeout(() => { stallTimer = null; room.socket.setBuffering(true) }, STALL_REPORT_MS)
    }
    const refresh = (asOfMs: number) => {
      refreshedFor = asOfMs
      // Whether or not it worked, attach again: a failure then shows in the player like any other.
      void room.refreshRoomDescriptor().catch(() => undefined).then(() => link.current.reload())
    }
    const seekLocal = (mediaTime: number) => { seekStarted = performance.now(); video.currentTime = mediaTime }
    const tick = () => {
      const p = link.current
      const { state, closed } = room.socket.snapshot
      if (!state || closed) return
      // The element says whether there is something to steer; the player's own state lags it by a render.
      const loading = p.state === 'resolving' || p.state === 'reconnecting' || video.readyState < HTMLMediaElement.HAVE_METADATA
      const stopped = p.state === 'error' || p.state === 'handoff'
      report(!stopped && (loading || video.readyState < HTMLMediaElement.HAVE_FUTURE_DATA))
      if (loading || stopped || p.audioBlocked || video.seeking) return
      const action = decide(state, room.clock.serverNow(), (p.offsetS + video.currentTime) * 1000, !video.paused, p.durationS > 0 ? p.durationS * 1000 : null)
      if (action.type === 'pause') p.pause()
      else if (action.type === 'play') {
        // An ended element would start over; a browser that refuses to start without a tap is not asked every tick.
        if (video.ended || performance.now() - lastPlayTry < 2000) return
        lastPlayTry = performance.now()
        p.resume()
      } else if (action.type === 'seek') {
        const target = (action.positionMs + (state.playing ? lead : 0)) / 1000
        if (p.restartable) {
          const plan = seekPlan(target, p.offsetS, seekableRanges(video), p.durationS)
          if ('within' in plan) seekLocal(plan.within)
          else if (refreshedFor !== state.as_of_ms) refresh(state.as_of_ms)
        } else if (video.seekable.length) {
          let closest = video.currentTime, distance = Infinity
          for (let i = 0; i < video.seekable.length; i++) {
            const candidate = Math.max(video.seekable.start(i), Math.min(video.seekable.end(i), target))
            if (Math.abs(candidate - target) < distance) { closest = candidate; distance = Math.abs(candidate - target) }
          }
          seekLocal(closest)
        }
      }
    }
    const onSeeked = () => {
      if (seekStarted && room.socket.snapshot.state?.playing) lead = Math.min(3000, Math.max(0, performance.now() - seekStarted))
      seekStarted = 0
      tick()
    }
    const offState = room.onState((state, previous) => {
      // A seek may have restarted the room's stream: a server-produced stream is asked for again, a plain file is moved in place.
      if (isSeek(previous, state) && link.current.restartable && refreshedFor !== state.as_of_ms) refresh(state.as_of_ms)
      else tick()
    })
    const timer = window.setInterval(tick, SYNC_INTERVAL_MS)
    const events = ['playing', 'pause', 'loadedmetadata', 'canplay', 'waiting'] as const
    for (const name of events) video.addEventListener(name, tick)
    video.addEventListener('seeked', onSeeked)
    tick()
    return () => {
      offState()
      window.clearInterval(timer)
      if (stallTimer !== null) window.clearTimeout(stallTimer)
      for (const name of events) video.removeEventListener(name, tick)
      video.removeEventListener('seeked', onSeeked)
    }
  }, [room, videoRef])

  // A host who brought a title they were already watching: the room starts where they were.
  useEffect(() => {
    if (!room || room.startAtMs === null || snapshot.status !== 'open' || !snapshot.state) return
    const at = room.startAtMs
    room.startAtMs = null
    if (isHost && !room.room.live && at > 0 && (snapshot.state.position_ms ?? 0) === 0 && !snapshot.state.playing) room.socket.seek(at)
  }, [room, isHost, snapshot.status, snapshot.state])

  // ---- this member's controls -> the room ----
  const allowed = useCallback((): boolean => {
    if (!room) return false
    const now = room.socket.snapshot
    if (now.closed) return false
    if (now.status !== 'open') { notice('Reconnecting to the room…'); return false }
    if (now.controlsLocked && !room.room.is_host) { notice('Only the host can control playback in this room.'); return false }
    return true
  }, [room, notice])
  const toggle = useCallback(() => {
    const video = videoRef.current
    if (!room || !video) return
    const state = room.socket.snapshot.state
    // The room has been playing for a while but this browser would not start without a tap: the tap starts it here.
    if (state?.playing && video.paused && !video.ended && room.clock.serverNow() - state.as_of_ms > AUTOPLAY_GRACE_MS) { link.current.resume(); return }
    if (!allowed()) return
    if (state?.playing) room.socket.pause(); else room.socket.play()
  }, [room, videoRef, allowed])
  const seekTo = useCallback((titleSeconds: number) => {
    if (!room || room.room.live || !allowed()) return
    const end = link.current.durationS > 0 ? Math.max(0, link.current.durationS - 1) : Infinity
    room.socket.seek(Math.max(0, Math.min(titleSeconds, end)) * 1000)
  }, [room, allowed])
  const seekBy = useCallback((seconds: number) => {
    const state = room?.socket.snapshot.state
    const at = state && room ? positionAt(state, room.clock.serverNow(), link.current.durationS > 0 ? link.current.durationS * 1000 : null) : null
    if (at !== null) seekTo(at / 1000 + seconds)
  }, [room, seekTo])

  const away = useCallback(() => {
    if (!room) return
    navigate(room.room.live ? '/live' : `/title/${encodeURIComponent(room.room.id)}`, { replace: true })
  }, [room, navigate])
  const leave = useCallback(() => { setLeaving(false); room?.socket.leave(); away() }, [room, away])
  const closed = snapshot.closed && snapshot.closed !== 'left' ? snapshot.closed : null
  useEffect(() => {
    if (!closed) return
    setLeaving(false); setSharing(false)
    const timer = window.setTimeout(() => void setFocus('room:closed:away'), 50)
    return () => window.clearTimeout(timer)
  }, [closed])

  if (!room) return null
  const lockedOut = snapshot.controlsLocked && !isHost
  const top: ReactNode = (
    <div className="room-strip" data-testid="room-members" role="group" aria-label={`In the room: ${snapshot.members.map((m) => m.name).join(', ')}`}>
      <Icon name="people" size={18} />
      {snapshot.members.map((m) => (
        <span key={m.id} className="room-member" data-testid="room-member" data-buffering={m.buffering} data-me={m.id === room.memberId}>
          {m.name}{m.host ? ' (host)' : ''}
          {m.buffering && <span className="room-buffering"><i aria-hidden="true" />buffering</span>}
        </span>
      ))}
      {lockedOut && <span className="room-member" data-testid="room-locked"><Icon name="lock" size={14} />Host controls playback</span>}
      {snapshot.status === 'reconnecting' && <span className="room-member" role="status">Reconnecting…</span>}
    </div>
  )
  const bar: ReactNode = <>
    {isHost && <Button focusKey="pb:lock" ghost icon="lock" pressed={snapshot.controlsLocked} onEnter={() => { if (!room.socket.lock(!snapshot.controlsLocked)) notice('Reconnecting to the room…') }} testId="room-lock">Only I can control</Button>}
    <Button focusKey="pb:invite" ghost icon="people" onEnter={() => setSharing(true)} testId="room-invite">Invite</Button>
    <Button focusKey="pb:leave" ghost icon="exit" onEnter={() => setLeaving(true)} testId="room-leave">Leave room</Button>
  </>
  const ended = closed ? CLOSED[closed] ?? { text: 'The room has closed.' } : null
  const layers: ReactNode = <>
    {snapshot.waiting && !closed && (
      <div className="room-waiting" role="status" aria-live="polite" data-testid="room-waiting">
        <i aria-hidden="true" />Waiting for {snapshot.waiting}…
      </div>
    )}
    {ended && (
      <div className="grid place-items-center p-6" style={{ position: 'absolute', inset: 0, zIndex: 9, background: 'rgba(0,0,0,.85)' }} role="alert" data-testid="room-closed" data-reason={closed}>
        <FocusGroup focusKey="room:closed" className="flex flex-col gap-4 items-start" style={{ maxWidth: '34rem' }}>
          <h1 className="title-xl">The room has ended for you</h1>
          <p>{ended.text}</p>
          <div className="flex gap-3 flex-wrap">
            <Button focusKey="room:closed:away" primary onEnter={away}>{room.room.live ? 'Back to Live TV' : `Back to ${room.room.title.title}`}</Button>
            {ended.rejoin && onRejoin && <Button focusKey="room:closed:rejoin" icon="refresh" onEnter={onRejoin} testId="room-rejoin">Rejoin</Button>}
          </div>
        </FocusGroup>
      </div>
    )}
    <Modal open={leaving} onClose={() => setLeaving(false)} title="Leave the room?" firstFocusKey="room:leave:stay" testId="room-leave-confirm">
      <p className="muted">{snapshot.members.length > 1 ? 'The others keep watching. You can come back with the same link or code.' : 'The room stays open for 30 minutes in case someone joins.'}</p>
      <div className="flex gap-3 flex-wrap">
        <Button focusKey="room:leave:stay" onEnter={() => setLeaving(false)}>Stay</Button>
        <Button focusKey="room:leave:go" danger icon="exit" onEnter={leave} testId="room-leave-go">Leave room</Button>
      </div>
    </Modal>
    <ShareSheet room={sharing ? room.room : null} onClose={() => setSharing(false)} />
  </>
  return {
    snapshot, toggle, seekTo, seekBy, top, bar, layers,
    askLeave: () => setLeaving(true),
    /** A room dialog has the keys: the player's own shortcuts stand down. */
    modal: leaving || sharing || !!closed,
  }
}
