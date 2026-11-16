import { useEffect, useRef, type RefObject } from 'react'
import { api, unwrap } from '../api/client'
import type { AlternateFeed, PlaybackAlternates } from '../api/types'
import { giveUp, nextFeed, observe, shouldSwitch, watchChannel, watchSwitched, type FeedWatch } from './feedFailover'
import type { PlayState } from './usePlayback'

/** How often the picture is looked at. */
const SAMPLE_MS = 1_000
/** A step of the playhead larger than this in one sample is a jump (a new stream, a seek), not play. */
const MAX_STEP_S = 3

/**
 * Watches a live channel's picture and moves to the next feed of the same game when it stops
 * (see feedFailover.ts for the rules). `onSwitch` tunes to the feed; the hook carries the failover
 * run across that change when the channel it is next given is the feed it asked for.
 */
export function useFeedFailover(videoRef: RefObject<HTMLVideoElement | null>, opts: {
  /** A live channel at its live edge, outside a watch room. */
  enabled: boolean
  channel: string
  state: PlayState
  /** Not meant to play right now although not paused by the viewer: an audio choice is pending. */
  held: boolean
  /** The error on screen is one no other feed would get past (access, an administrator's stop). */
  refused: boolean
  onSwitch: (to: AlternateFeed, from: string) => void
}) {
  const latest = useRef(opts)
  latest.current = opts
  const watch = useRef<FeedWatch | null>(null)
  // The switch asked for: the run continues when the player arrives on this channel.
  const pending = useRef<{ from: FeedWatch; to: string } | null>(null)
  const { enabled, channel } = opts

  useEffect(() => {
    if (!enabled) { watch.current = null; return }
    const now = performance.now()
    const switched = pending.current
    pending.current = null
    if (watch.current?.channel === channel) return
    watch.current = switched?.to === channel ? watchSwitched(switched.from, channel, now) : watchChannel(channel, now)
  }, [enabled, channel])

  useEffect(() => {
    if (!enabled) return
    let last: number | null = null
    let asking = false
    let stopped = false
    const tick = () => {
      const w = watch.current
      const v = videoRef.current
      if (!w || !v || asking || w.channel !== latest.current.channel) return
      const { state, held, refused } = latest.current
      const t = v.currentTime
      const step = last === null ? 0 : t - last
      last = t
      const progressing = !v.paused && step > 0 && step <= MAX_STEP_S
      // An error another feed would only repeat is the player's to show, like a pause is the viewer's.
      const paused = state === 'paused' || state === 'handoff' || held || (state === 'error' && refused)
      const now = performance.now()
      const next = observe(w, { now, progressing, paused })
      watch.current = next
      if (!shouldSwitch(next, now)) return
      asking = true
      // Asked now, not when the channel started: what carries the game changes through an evening.
      unwrap<PlaybackAlternates>(api.GET('/playback/alternates', { params: { query: { id: next.channel } } }))
        .then((found) => {
          if (stopped || watch.current !== next) return
          const feed = nextFeed(next, found.alternates)
          if (!feed) { watch.current = giveUp(next); return }
          pending.current = { from: next, to: feed.channel_id }
          latest.current.onSwitch(feed, next.channel)
        })
        .catch(() => { if (!stopped && watch.current === next) watch.current = giveUp(next) })
        .finally(() => { asking = false })
    }
    const timer = window.setInterval(tick, SAMPLE_MS)
    return () => { stopped = true; window.clearInterval(timer) }
  }, [enabled, channel, videoRef])
}
