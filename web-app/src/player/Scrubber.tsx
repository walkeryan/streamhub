import { useRef, useState, type PointerEvent } from 'react'
import { Focusable } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { fmtDuration } from '../lib/util'

const pct = (seconds: number, duration: number) => `${duration > 0 ? Math.min(100, Math.max(0, (seconds / duration) * 100)) : 0}%`

/**
 * The seek bar. Its scale is the whole title, not what the stream has produced so far.
 *
 * A pointer shows the time under it, previews a drag with a second thumb and seeks ONCE, on release:
 * a seek outside what is loaded restarts the server's transcoder, so nothing is sent while dragging.
 * `target` is a seek the player has not made yet (key presses still adding up); `busy` is one it has
 * made and is waiting for, in which case `position` already is where playback is headed.
 */
export function Scrubber(p: {
  /** Seconds into the title. */
  position: number
  duration: number
  /** What can be played without asking the server again, in title seconds. */
  loaded: ReadonlyArray<readonly [number, number]>
  target: number | null
  /** "+40 s": how far the target is from where the presses began. */
  hint: string | null
  busy: boolean
  disabled: boolean
  onSeek: (titleSeconds: number) => void
  onStep: (dir: -1 | 1) => void
  onEnter: () => void
}) {
  const track = useRef<HTMLDivElement>(null)
  // Fractions of the bar: where the pointer rests, and where it is being dragged to.
  const [hover, setHover] = useState<number | null>(null)
  const [drag, setDrag] = useState<number | null>(null)
  const dragging = useRef(false)
  const at = (e: PointerEvent) => {
    const box = track.current?.getBoundingClientRect()
    return box && box.width > 0 ? Math.min(1, Math.max(0, (e.clientX - box.left) / box.width)) : 0
  }
  const usable = !p.disabled && p.duration > 0
  const ghost = drag !== null ? drag * p.duration : p.target
  const bubble = drag !== null ? drag * p.duration : p.target ?? (hover !== null ? hover * p.duration : null)
  // The bubble slides along with its own width (left edge at the start, right edge at the end), so it never leaves the bar.
  const text = `${fmtDuration(p.position)} of ${fmtDuration(p.duration)}`
  return (
    <Focusable focusKey="pb:scrub" role="slider" className="scrub fx-none" disabled={!usable} scroll={false}
      label={usable ? `Seek. ${text}. Left and right move ten seconds, hold to move faster, OK goes there.` : 'This stream has not supplied a seekable range.'}
      aria={{ valuemin: 0, valuemax: Math.round(p.duration), valuenow: Math.round(ghost ?? p.position), valuetext: ghost !== null ? `${fmtDuration(ghost)} of ${fmtDuration(p.duration)}` : text, orientation: 'horizontal' }}
      onArrow={(direction) => { if (direction === 'left' || direction === 'right') { p.onStep(direction === 'left' ? -1 : 1); return false } return true }}
      onEnter={p.onEnter}>
      <div ref={track} className="scrub-hit" data-testid="scrub-track" data-busy={p.busy} data-dragging={drag !== null} data-position={Math.round(p.position)} data-target={ghost === null ? undefined : Math.round(ghost)}
        // The Focusable's click means OK (play or pause); a click on the bar is a seek, made on release below.
        onClick={(e) => e.stopPropagation()}
        onPointerDown={(e) => {
          if (!usable || (e.pointerType === 'mouse' && e.button !== 0)) return
          e.currentTarget.setPointerCapture?.(e.pointerId)
          dragging.current = true
          setDrag(at(e))
          if (e.pointerType !== 'mouse') void setFocus('pb:scrub')
        }}
        onPointerMove={(e) => {
          if (!usable) return
          if (dragging.current) setDrag(at(e))
          else if (e.pointerType === 'mouse') setHover(at(e))
        }}
        onPointerUp={(e) => {
          if (!dragging.current) return
          dragging.current = false
          setDrag(null)
          // A mouse is still over the bar where it let go; a finger has left.
          setHover(e.pointerType === 'mouse' ? at(e) : null)
          p.onSeek(at(e) * p.duration)
        }}
        onPointerCancel={() => { dragging.current = false; setDrag(null); setHover(null) }}
        onPointerLeave={() => { if (!dragging.current) setHover(null) }}>
        <div className="scrub-rail">
          {!p.busy && p.loaded.map(([from, to], i) => (
            <i key={i} className="scrub-loaded" data-testid="scrub-loaded" style={{ left: pct(from, p.duration), width: pct(Math.max(0, to - from), p.duration) }} />
          ))}
          <i className="scrub-played" data-testid="scrub-played" style={{ width: pct(p.position, p.duration) }} />
        </div>
        {usable && <span className="scrub-thumb" data-testid="scrub-thumb" data-busy={p.busy} style={{ left: pct(p.position, p.duration) }} />}
        {usable && ghost !== null && <span className="scrub-thumb scrub-ghost" data-testid="scrub-ghost" style={{ left: pct(ghost, p.duration) }} />}
        {usable && bubble !== null && (
          <span className="scrub-bubble" data-testid="scrub-bubble" style={{ left: pct(bubble, p.duration), transform: `translateX(-${pct(bubble, p.duration)})` }}>
            {fmtDuration(bubble)}{drag === null && p.target !== null && p.hint ? <b>{p.hint}</b> : null}
          </span>
        )}
      </div>
    </Focusable>
  )
}
