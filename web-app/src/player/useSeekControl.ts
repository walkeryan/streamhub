import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import { seekPlan, seekableRanges } from './restartSeek'
import { COMMIT_MS, accumulate, clampTarget, jump, type PendingSeek } from './seekSteps'

/** How long a room is given to move everyone before the bar stops showing the place it was asked for. */
const ROOM_SEEK_SHOWN_MS = 6000

export interface SeekTarget {
  /** Seeking is possible at all (a seekable stream, a title whose length is known). */
  enabled: boolean
  /** Seconds into the title at which the media element's time zero lies. */
  offsetS: number
  /** The title's length in seconds, 0 when unknown. */
  durationS: number
  /** A restart-at-offset stream: outside what it has produced only a new stream helps. */
  restartable: boolean
  /** Start a new stream at this title position (milliseconds). */
  restart: (ms: number) => void
  /** A watch room: every seek goes to the room, which moves all its members. */
  room: { seekTo: (titleSeconds: number) => void; canControl: () => boolean } | null
}

/**
 * The player's seeking. Steps and jumps add up to one pending target (seekSteps.ts) that is committed
 * when the presses pause or on Enter; a drag or a click commits at once. A target inside what the
 * element already holds costs nothing, so the picture follows it straight away; anything else waits
 * for the commit and is then shown as `heading` until the new stream says where it began.
 */
export function useSeekControl(videoRef: RefObject<HTMLVideoElement | null>, target: SeekTarget, streamKey: unknown, failed: boolean) {
  const [pending, setPendingState] = useState<PendingSeek | null>(null)
  const pendingRef = useRef<PendingSeek | null>(null)
  const setPending = (next: PendingSeek | null) => { pendingRef.current = next; setPendingState(next) }
  // Where a committed seek is headed (title seconds), shown instead of the old position until it lands.
  const [heading, setHeadingState] = useState<number | null>(null)
  const headingRef = useRef<number | null>(null)
  const setHeading = useCallback((next: number | null) => { headingRef.current = next; setHeadingState(next) }, [])
  const timer = useRef(0)
  const roomTimer = useRef(0)
  const t = useRef(target)
  t.current = target

  const position = useCallback(() => headingRef.current ?? t.current.offsetS + (videoRef.current?.currentTime ?? 0), [videoRef])
  /** The media-element time for a title position the element can reach by itself, else null. */
  const local = useCallback((title: number): number | null => {
    const v = videoRef.current
    const o = t.current
    if (!v || o.room) return null
    if (o.restartable) {
      const plan = seekPlan(title, o.offsetS, seekableRanges(v), o.durationS)
      return 'within' in plan ? plan.within : null
    }
    if (!v.seekable.length) return null
    let closest = v.currentTime, distance = Infinity
    for (let i = 0; i < v.seekable.length; i++) {
      const candidate = Math.max(v.seekable.start(i), Math.min(v.seekable.end(i), title))
      if (Math.abs(candidate - title) < distance) { closest = candidate; distance = Math.abs(candidate - title) }
    }
    return closest
  }, [videoRef])

  /** One committed seek to a title position. */
  const seekNow = useCallback((title: number) => {
    window.clearTimeout(timer.current)
    setPending(null)
    const o = t.current
    const v = videoRef.current
    if (!o.enabled || !v) return
    const wanted = clampTarget(title, o.durationS)
    if (o.room) {
      if (!o.room.canControl()) return
      setHeading(wanted)
      window.clearTimeout(roomTimer.current)
      roomTimer.current = window.setTimeout(() => setHeading(null), ROOM_SEEK_SHOWN_MS)
      o.room.seekTo(wanted)
      return
    }
    const within = local(wanted)
    if (within !== null) { if (Math.abs(v.currentTime - within) > 0.25) v.currentTime = within; return }
    if (!o.restartable) return
    setHeading(wanted)
    o.restart(Math.floor(wanted * 1000))
  }, [videoRef, local, setHeading])

  const commit = useCallback(() => {
    const p = pendingRef.current
    if (p) seekNow(p.target)
  }, [seekNow])

  const move = useCallback((next: PendingSeek) => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(commit, COMMIT_MS)
    const before = pendingRef.current
    pendingRef.current = next
    // A held key presses more often than the target moves.
    if (before && before.target === next.target) return
    setPendingState(next)
    // Free inside what is loaded: the picture need not wait for the presses to stop.
    const v = videoRef.current
    const within = headingRef.current === null ? local(next.target) : null
    if (v && within !== null) v.currentTime = within
  }, [videoRef, commit, local])

  const ready = useCallback(() => {
    const o = t.current
    return o.enabled && (!o.room || o.room.canControl())
  }, [])
  /** One press of a step key or button. */
  const step = useCallback((dir: -1 | 1, how: { held?: boolean; big?: boolean } = {}) => {
    if (!pendingRef.current && !ready()) return
    move(accumulate(pendingRef.current, { dir, now: performance.now(), ...how }, position(), t.current.durationS))
  }, [move, position, ready])
  /** A jump from the keyboard: waits like a step, so 3 then 5 is one seek. */
  const jumpTo = useCallback((title: number) => {
    if (!pendingRef.current && !ready()) return
    move(jump(pendingRef.current, title, position(), t.current.durationS, performance.now()))
  }, [move, position, ready])
  /** Drop the pending target. True when there was one. */
  const cancel = useCallback((): boolean => {
    const p = pendingRef.current
    if (!p) return false
    window.clearTimeout(timer.current)
    setPending(null)
    // The picture may have followed the target inside what was loaded: put it back where the presses began.
    const v = videoRef.current
    const back = headingRef.current === null ? local(p.base) : null
    if (v && back !== null && Math.abs(v.currentTime - back) > 0.25) v.currentTime = back
    return true
  }, [videoRef, local])

  // A new stream says where it began; a failed one is not headed anywhere.
  useEffect(() => { setHeading(null) }, [streamKey, setHeading])
  useEffect(() => { if (failed) setHeading(null) }, [failed, setHeading])
  // A room seek inside what a member has loaded changes no stream: the element arriving is the news.
  const inRoom = !!target.room
  useEffect(() => {
    const v = videoRef.current
    if (!v || !inRoom) return
    const landed = () => { window.clearTimeout(roomTimer.current); setHeading(null) }
    v.addEventListener('seeked', landed)
    return () => v.removeEventListener('seeked', landed)
  }, [videoRef, inRoom, setHeading])
  useEffect(() => () => { window.clearTimeout(timer.current); window.clearTimeout(roomTimer.current) }, [])

  return { pending, heading, setHeading, step, jumpTo, seekNow, commit, cancel, hasPending: () => pendingRef.current !== null }
}
