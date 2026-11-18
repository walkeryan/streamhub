import { useState } from 'react'
import type { Channel, Programme } from '../api/types'
import { Button, GlassIconButton, Icon, Modal } from '../ds'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { fmtTime } from '../lib/util'

export interface GuideDetailsProps {
  /** The focused channel, and the programme focused on it (or what it has on now). Null: nothing to describe yet. */
  channel: Channel | null
  programme: Programme | null
  now: number
  recordable: boolean
  /** The programme's recording in words: "Will record", or why it failed. */
  recording: string | null
  hint: string
  onWatch: (channel: Channel) => void
  onRecord: (channel: Channel, programme: Programme) => void
  onCatchup: (channel: Channel, programme: Programme) => void
}

const metaOf = (c: Channel, p: Programme | null) =>
  [p && `${fmtTime(p.start)} to ${fmtTime(p.stop)}`, p?.category, `${c.number} ${c.name}`].filter(Boolean).join(' · ')
/** The same words as a meta line: time, category, channel, spaced like the title page's. */
const Meta = ({ c, p, className }: { c: Channel; p: Programme | null; className?: string }) =>
  <p className={className ? `ds-meta ${className}` : 'ds-meta'}>{metaOf(c, p)}</p>

/** What can be done with the programme: the same three things OK on its cell leads to. Watch is the one
 * primary, with words; Record and Catch-up are glass icons named by their labels (a tooltip on focus). */
function Actions({ p, scope, after, tipAt }: { p: GuideDetailsProps; scope: string; after?: () => void; tipAt?: 'above' | 'below' }) {
  const { channel: c, programme } = p
  if (!c) return null
  const over = !!programme && Date.parse(programme.stop) <= p.now
  const act = (run: () => void) => () => { after?.(); run() }
  return <>
    <Button focusKey={`${scope}:watch`} className="epg-watch" primary icon="play" onEnter={act(() => p.onWatch(c))} testId="guide-watch">Watch</Button>
    {programme && !over && p.recordable && <GlassIconButton focusKey={`${scope}:record`} size="sm" icon="record" label="Record" tipAt={tipAt} onEnter={act(() => p.onRecord(c, programme))} testId="guide-record" />}
    {programme && over && programme.catchup_available && <GlassIconButton focusKey={`${scope}:catchup`} size="sm" icon="restart" label="Catch-up" tipAt={tipAt} onEnter={act(() => p.onCatchup(c, programme))} testId="guide-catchup" />}
  </>
}

/**
 * Wide screens: the focused programme beside the Live TV header. Every line has a fixed share of a
 * fixed height, so moving over programmes with long or short descriptions never moves the grid.
 */
export function GuideDetailsPanel(p: GuideDetailsProps) {
  const { channel: c, programme } = p
  return (
    <FocusGroup focusKey="guide:details" className="epg-details" role="region" label="Programme details" data-testid="guide-info">
      {c ? <>
        <h2 className="epg-details-title truncate" aria-live="polite">{programme?.title ?? c.name}</h2>
        <div className="epg-details-actions"><Actions p={p} scope="guide:details" /></div>
        <Meta c={c} p={programme} className="epg-details-meta" />
        <p className="label clamp2 epg-details-text">
          {p.recording && <><span data-testid="guide-recording" style={{ color: 'var(--t-text)' }}>{p.recording}</span>{programme?.description ? ' · ' : ''}</>}
          {programme ? programme.description : 'No programme data supplied for this channel.'}
        </p>
      </> : <p className="muted clamp2 epg-details-hint">{p.hint}</p>}
    </FocusGroup>
  )
}

/** Narrower screens and phones: one line under the header; OK or a tap opens the rest in a sheet. */
export function GuideDetailsLine(p: GuideDetailsProps) {
  const [open, setOpen] = useState(false)
  const { channel: c, programme } = p
  // One line has no room for the panel's hint, and a finger does not "move over" anything.
  if (!c) return <p className="epg-details-line label" data-testid="guide-info"><span className="truncate">Select a programme for its details.</span></p>
  const title = programme?.title ?? c.name
  return <>
    <Focusable focusKey="guide:details" className="epg-details-line fx-flat" label={`Details: ${title}`} data-testid="guide-info" onEnter={() => setOpen(true)}>
      <span className="epg-details-line-title truncate">{title}</span>
      <span className="label truncate epg-details-line-meta">{p.recording ? `${p.recording} · ` : ''}{metaOf(c, programme)}</span>
      <Icon name="chevR" size={14} />
    </Focusable>
    <Modal open={open} onClose={() => setOpen(false)} title={title} firstFocusKey="guide:sheet:watch" variant="sheet" testId="guide-sheet">
      <Meta c={c} p={programme} />
      {p.recording && <p className="label" data-testid="guide-recording">{p.recording}</p>}
      <p className="epg-sheet-text">{programme ? programme.description : 'No programme data supplied for this channel.'}</p>
      <FocusGroup focusKey="guide:sheet" className="epg-sheet-actions">
        <Actions p={p} scope="guide:sheet" after={() => setOpen(false)} tipAt="above" />
        <Button focusKey="guide:sheet:close" className="epg-sheet-close" ghost onEnter={() => setOpen(false)}>Close</Button>
      </FocusGroup>
    </Modal>
  </>
}
