import { useState } from 'react'
import type { Channel, Programme } from '../api/types'
import { Button, Modal } from '../components/ui'
import { Icon } from '../components/Icon'
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

/** What can be done with the programme: the same three things OK on its cell leads to, by name. */
function Actions({ p, scope, after }: { p: GuideDetailsProps; scope: string; after?: () => void }) {
  const { channel: c, programme } = p
  if (!c) return null
  const over = !!programme && Date.parse(programme.stop) <= p.now
  const act = (run: () => void) => () => { after?.(); run() }
  return <>
    <Button focusKey={`${scope}:watch`} className="btn-chip" primary icon="play" onEnter={act(() => p.onWatch(c))} testId="guide-watch">Watch</Button>
    {programme && !over && p.recordable && <Button focusKey={`${scope}:record`} className="btn-chip" icon="record" onEnter={act(() => p.onRecord(c, programme))} testId="guide-record">Record</Button>}
    {programme && over && programme.catchup_available && <Button focusKey={`${scope}:catchup`} className="btn-chip" icon="refresh" onEnter={act(() => p.onCatchup(c, programme))} testId="guide-catchup">Catch-up</Button>}
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
        <h2 className="heading truncate" aria-live="polite">{programme?.title ?? c.name}</h2>
        <div className="epg-details-actions"><Actions p={p} scope="guide:details" /></div>
        <p className="label truncate epg-details-meta">{metaOf(c, programme)}</p>
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
  if (!c) return <p className="epg-details-line label" data-testid="guide-info"><span className="truncate">{p.hint}</span></p>
  const title = programme?.title ?? c.name
  return <>
    <Focusable focusKey="guide:details" className="epg-details-line fx-flat" label={`Details: ${title}`} data-testid="guide-info" onEnter={() => setOpen(true)}>
      <span className="truncate" style={{ fontWeight: 600, flex: 'none', maxWidth: '55%' }}>{title}</span>
      <span className="label truncate" style={{ flex: 1 }}>{p.recording ? `${p.recording} · ` : ''}{metaOf(c, programme)}</span>
      <Icon name="chevR" size={14} />
    </Focusable>
    <Modal open={open} onClose={() => setOpen(false)} title={title} firstFocusKey="guide:sheet:watch" variant="sheet" testId="guide-sheet">
      <p className="muted">{metaOf(c, programme)}</p>
      {p.recording && <p className="label" data-testid="guide-recording">{p.recording}</p>}
      <p>{programme ? programme.description : 'No programme data supplied for this channel.'}</p>
      <FocusGroup focusKey="guide:sheet" className="flex gap-2 flex-wrap">
        <Actions p={p} scope="guide:sheet" after={() => setOpen(false)} />
        <Button focusKey="guide:sheet:close" className="btn-chip" onEnter={() => setOpen(false)}>Close</Button>
      </FocusGroup>
    </Modal>
  </>
}
