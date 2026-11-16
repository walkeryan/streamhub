/**
 * Moving on to another feed of a game when the one being watched stops.
 *
 * A game is often carried by several channels at once. When the live channel being watched is
 * one of them and it stops working for longer than `STALL_SWITCH_MS` while the viewer wants it to
 * play, the player switches to the next feed of the same game (`GET /playback/alternates`). A run of
 * failures visits each feed at most once, in the order the server gives (the feeds after this
 * one, wrapping round); once every feed has been tried the player's own reconnect and error
 * handling take over. A feed that plays steadily for `STEADY_RESET_MS` ends the run. Nothing
 * here ever goes back to a feed because it recovered.
 *
 * "Stops working" means no new video is arriving: neither does the picture move nor does the
 * buffered video ahead of it grow (or the stream failed). A frozen picture while video still
 * arrives (the live cushion holding for its reserve after a gap, a slow start) is the feed
 * recovering, not failing, so it never counts towards a switch.
 *
 * Start-up is not a stall: a channel the viewer chose is watched only once it has played. A feed
 * this run switched to gets `STARTUP_GRACE_MS` to start; one that has not played by then failed too.
 *
 * Pure state over plain numbers (milliseconds of any monotonic clock), shared in spirit with the
 * TV app's FeedFailover object: the same constants and the same rules.
 */

/** No picture progress and no new video for longer than this, while the viewer wants to play, is a stall. */
export const STALL_SWITCH_MS = 5_000
/** How long a feed switched to automatically has to start before it counts as failed too. */
export const STARTUP_GRACE_MS = 10_000
/** A feed that plays this long without a stall ends the failover run. */
export const STEADY_RESET_MS = 30_000

export interface FeedWatch {
  /** The channel being watched. */
  channel: string
  /** When it started (the viewer's choice, or the switch to it). */
  startedAt: number
  /** Its picture has moved at least once since then. */
  played: boolean
  /** Since when neither picture nor video has moved while meant to play; null while it plays, receives or is paused. */
  troubleSince: number | null
  /** Since when it has played without a break; null otherwise. */
  steadySince: number | null
  /** The failover run: the channels tried in it, the one that first failed first. Empty: no run. */
  tried: string[]
  /** Every feed of this run has been tried, or there was none to try: the player's own recovery is in charge. */
  exhausted: boolean
}

/** A channel the viewer chose (a zap, a number, the list): a fresh watch, no run. */
export function watchChannel(channel: string, now: number): FeedWatch {
  return { channel, startedAt: now, played: false, troubleSince: null, steadySince: null, tried: [], exhausted: false }
}

/** The feed a switch moved to: the run goes on with it. */
export function watchSwitched(previous: FeedWatch, channel: string, now: number): FeedWatch {
  const tried = previous.tried.length ? previous.tried : [previous.channel]
  return { ...watchChannel(channel, now), tried: tried.includes(channel) ? tried : [...tried, channel] }
}

export interface Observation {
  now: number
  /** The picture moved since the last observation. */
  progressing: boolean
  /** New video arrived since the last observation (the buffered video ahead grew); false once the stream failed. */
  arriving: boolean
  /** Not meant to play: paused by the viewer, waiting for a tap to start, held for an audio choice. */
  paused: boolean
}

export function observe(watch: FeedWatch, { now, progressing, arriving, paused }: Observation): FeedWatch {
  if (progressing) {
    const steadySince = watch.steadySince ?? now
    // Long enough on one feed: a later stall is a new run, which may visit every feed again.
    const settled = (watch.tried.length > 0 || watch.exhausted) && now - steadySince >= STEADY_RESET_MS
    return { ...watch, played: true, troubleSince: null, steadySince, ...(settled ? { tried: [], exhausted: false } : {}) }
  }
  // Held or starting while video comes in: not trouble, but not play either (neither played nor steady).
  if (paused || arriving) return { ...watch, troubleSince: null, steadySince: null }
  return { ...watch, steadySince: null, troubleSince: watch.troubleSince ?? now }
}

/** Is it time to move on to another feed? */
export function shouldSwitch(watch: FeedWatch, now: number): boolean {
  if (watch.exhausted || watch.troubleSince === null) return false
  // A channel the viewer just chose is starting, not stalling; one this run moved to has a grace to start.
  if (!watch.played) return watch.tried.length > 0 && now - watch.startedAt > STARTUP_GRACE_MS
  return now - watch.troubleSince > STALL_SWITCH_MS
}

/** The first feed of `alternates` (already in the server's order) this run has not tried, or null. */
export function nextFeed<T extends { channel_id: string }>(watch: FeedWatch, alternates: readonly T[]): T | null {
  const tried = new Set(watch.tried.length ? watch.tried : [watch.channel])
  tried.add(watch.channel)
  return alternates.find((feed) => !tried.has(feed.channel_id)) ?? null
}

/** Nothing (more) to switch to: leave the stall to the player's own recovery until a feed plays steadily again. */
export function giveUp(watch: FeedWatch): FeedWatch {
  return { ...watch, exhausted: true }
}

/** The notice shown when the player moved on. */
export function switchNotice(from: string, to: string): string {
  return `${from} stopped — switched to ${to}`
}
