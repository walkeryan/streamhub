import { useEffect } from 'react'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { SafeImg } from '../components/SafeImg'
import { Icon } from '../ds'
import { COUNTDOWN_MS, countdownSeconds } from './upNext'
import type { PlaybackDescriptor } from '../api/types'

type UpNext = NonNullable<PlaybackDescriptor['next']>

/** Skip intro / Skip credits: white, ringed in the accent, bottom right (mockup 3). */
export function SkipButton({ label, onSkip, testId }: { label: string; onSkip: () => void; testId: string }) {
  return (
    <Focusable focusKey="px:skip" className="pskip" onEnter={onSkip} label={label} data-testid={testId} scroll={false}>
      <span>{label}</span><Icon name="skip" size={22} />
    </Focusable>
  )
}

/**
 * The Up next card: the next episode's still with the countdown ring, "Up next · S2 · E2" and its name.
 * Without autoplay it offers "Play next" and counts nothing. `tv`: a remote's words (Back) in the hint.
 */
export function UpNextCard(p: {
  next: UpNext; remainingMs: number; autoplay: boolean; tv: boolean
  onPlay: () => void; onKeepWatching: () => void
}) {
  const seconds = countdownSeconds(p.remainingMs)
  const done = 1 - p.remainingMs / COUNTDOWN_MS
  const r = 24, c = 2 * Math.PI * r
  return (
    <FocusGroup focusKey="px:upnext" className="pupnext" data-testid="up-next" data={{ autoplay: p.autoplay }} label={`Up next: ${p.next.title}`}>
      <Focusable focusKey="px:upnext:play" className="pupnext-thumb" onEnter={p.onPlay} label={`Play ${p.next.title} now`} scroll={false} data-testid="up-next-play">
        <SafeImg src={p.next.image} alt="" fallback={<span className="pupnext-noart" />} />
        {p.autoplay ? (
          <span className="pupnext-ring" aria-hidden="true">
            <svg viewBox="0 0 56 56"><circle cx="28" cy="28" r={r} className="pupnext-track" /><circle cx="28" cy="28" r={r} className="pupnext-arc" strokeDasharray={c} strokeDashoffset={c * (1 - done)} /></svg>
            <b data-testid="up-next-count">{seconds}</b>
          </span>
        ) : <span className="pupnext-ring pupnext-play" aria-hidden="true"><Icon name="play" size={22} /></span>}
      </Focusable>
      <div className="pupnext-text">
        <small>Up next{p.next.subtitle ? ` · ${p.next.subtitle}` : ''}</small>
        <b className="truncate" data-testid="up-next-title">{p.next.title}</b>
        {p.autoplay
          ? <small role="status">Plays in {seconds} s{p.tv ? ' · press Back to keep watching' : ''}</small>
          : <Focusable focusKey="px:upnext:next" className="pupnext-btn pupnext-primary" onEnter={p.onPlay} scroll={false} data-testid="up-next-play-next">Play next</Focusable>}
        {!(p.autoplay && p.tv) && <Focusable focusKey="px:upnext:keep" className="pupnext-btn" onEnter={p.onKeepWatching} scroll={false} data-testid="up-next-keep">Keep watching</Focusable>}
      </div>
    </FocusGroup>
  )
}

/** Still watching? After STILL_WATCHING_AFTER autoplays with nobody touching anything, the player waits here. */
export function StillWatching({ title, image, onContinue, onLeave, leaveLabel }: {
  title: string; image: string | null | undefined; onContinue: () => void; onLeave: () => void; leaveLabel: string
}) {
  // A remote lands on Continue watching.
  useEffect(() => { const t = window.setTimeout(() => void setFocus('px:still:go'), 30); return () => window.clearTimeout(t) }, [])
  return (
    <div className="pstill" role="dialog" aria-modal="true" aria-labelledby="pstill-h" data-testid="still-watching">
      {image && <SafeImg className="pstill-art" src={image} alt="" fallback={null} />}
      <FocusGroup focusKey="px:still" boundary className="pstill-box">
        <h2 id="pstill-h" className="display">Still watching?</h2>
        <p>{title} is paused. Playback continues when you are back.</p>
        <div className="pstill-actions">
          <Focusable focusKey="px:still:go" className="pupnext-btn pupnext-primary" onEnter={onContinue} data-testid="still-continue"><Icon name="play" size={20} />Continue watching</Focusable>
          <Focusable focusKey="px:still:leave" className="pupnext-btn" onEnter={onLeave} data-testid="still-leave">{leaveLabel}</Focusable>
        </div>
      </FocusGroup>
    </div>
  )
}
