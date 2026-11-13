import { useEffect, useState } from 'react'
import { useCreateRecordingRule, useDeleteRecording, useRecordings, useScheduleRecording } from '../api/hooks'
import type { Channel, Programme, RecordingRule } from '../api/types'
import { Button, Chip, Modal } from '../components/ui'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { looksLikeSport, recordingOf, seriesTitle, teamFrom } from '../lib/recordings'
import { fmtTime } from '../lib/util'
import { useUi } from '../store/ui'
import { RecordingBadge } from './RecordingBadge'

export interface RecordTarget { channel: Pick<Channel, 'id' | 'name'>; programmes: Programme[] }

/**
 * Record from the guide: this programme, every new episode of it, every game of a team, or (advanced)
 * anything whose title contains a word. Opened on one programme, or on a channel's now and next.
 * `onWatch` adds Watch now for a programme that is on the air.
 */
export function RecordDialog({ target, onClose, onWatch }: { target: RecordTarget | null; onClose: () => void; onWatch?: (p: Programme) => void }) {
  const toast = useUi((s) => s.toast)
  const recordings = useRecordings(!!target, false)
  const schedule = useScheduleRecording()
  const cancel = useDeleteRecording()
  const createRule = useCreateRecordingRule()
  const [index, setIndex] = useState(0)
  const [team, setTeam] = useState('')
  const [keyword, setKeyword] = useState('')
  const [advanced, setAdvanced] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const p = target?.programmes[Math.min(index, (target?.programmes.length ?? 1) - 1)]
  const key = target ? `${target.channel.id}|${target.programmes.map((x) => x.id).join(',')}` : ''
  useEffect(() => { setIndex(0); setAdvanced(false); setKeyword(''); setError(null) }, [key])
  useEffect(() => { if (p) setTeam(teamFrom(p.title)) }, [p?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!target || !p) return null

  const busy = schedule.isPending || createRule.isPending || cancel.isPending
  const existing = recordingOf(recordings.data?.recordings, p)
  const active = p.recording_state === 'scheduled' || p.recording_state === 'recording'
  const onAir = Date.parse(p.start) <= Date.now() && Date.parse(p.stop) > Date.now()
  const sport = looksLikeSport(p)
  const failed = (e: Error) => { setError(e.message); void setFocus(active ? 'rec:cancel' : 'rec:one') }
  const done = (text: string) => { toast(text, 'success'); onClose() }
  const one = () => {
    if (busy) return
    setError(null)
    schedule.mutate({ programme_id: p.id, channel_id: p.channel_id, start: p.start }, { onSuccess: (r) => done(r.state === 'recording' ? `Recording ${r.title} now.` : `${r.title} will be recorded at ${fmtTime(r.starts_at)}.`), onError: failed })
  }
  const rule = (kind: RecordingRule['kind'], match: string, body: { channel_id?: string | null; new_only?: boolean }, said: string) => {
    const text = match.trim()
    if (busy || !text) return
    setError(null)
    createRule.mutate({ kind, match: text, channel_id: body.channel_id ?? null, new_only: !!body.new_only, keep: null }, { onSuccess: () => done(said.replace('{match}', text)), onError: failed })
  }
  const stop = () => {
    if (busy || !existing) return
    setError(null)
    cancel.mutate(existing.id, { onSuccess: () => done(existing.state === 'recording' ? `Stopped recording ${existing.title}.` : `${existing.title} will not be recorded.`), onError: failed })
  }
  return (
    <Modal open onClose={onClose} title={p.title} firstFocusKey={onWatch && onAir ? 'rec:watch' : active ? 'rec:cancel' : 'rec:one'} variant="sheet" testId="record-dialog">
      {target.programmes.length > 1 && (
        <FocusGroup focusKey="rec:which" className="flex gap-2 flex-wrap" role="group" label="Programme">
          {target.programmes.map((x, i) => <Chip key={x.id} focusKey={`rec:which:${i}`} pressed={x.id === p.id} onEnter={() => { setIndex(i); setError(null) }}>{i === 0 ? 'Now' : 'Next'} · {x.title}</Chip>)}
        </FocusGroup>
      )}
      <p className="muted flex items-center gap-2 flex-wrap">{target.channel.name} · {fmtTime(p.start)} to {fmtTime(p.stop)}{p.category ? ` · ${p.category}` : ''}<RecordingBadge state={p.recording_state} /></p>
      {error && <p role="alert" data-testid="record-error" style={{ color: 'var(--t-danger)' }}>{error}</p>}
      <FocusGroup focusKey="rec:choices" className="flex flex-col gap-2">
        {onWatch && onAir && <Button focusKey="rec:watch" primary icon="play" onEnter={() => { onClose(); onWatch(p) }} testId="record-watch">Watch now</Button>}
        {active
          ? <Button focusKey="rec:cancel" danger icon="x" disabled={busy || !existing} onEnter={stop} testId="record-cancel">{p.recording_state === 'recording' ? 'Stop recording' : 'Do not record this programme'}</Button>
          : <Button focusKey="rec:one" primary={!(onWatch && onAir)} icon="record" disabled={busy} onEnter={one} testId="record-one">Record this programme</Button>}
        <Button focusKey="rec:series" className="rec-choice" disabled={busy} onEnter={() => rule('series', seriesTitle(p.title), { channel_id: p.channel_id, new_only: true }, 'Every new episode of {match} will be recorded.')} testId="record-series">
          <span className="flex flex-col items-start min-w-0"><span>Record every new episode</span><span className="label">{seriesTitle(p.title)} on {target.channel.name}, repeats skipped</span></span>
        </Button>
        {sport && (
          <div className="rec-option" data-testid="record-team">
            <span className="label">Record every game for</span>
            <div className="flex gap-2 flex-wrap items-center">
              <div className="flex-1" style={{ minWidth: '10rem' }}><FocusableInput focusKey="rec:team" label="Team" placeholder="Team name" maxLength={80} value={team} onChange={setTeam} onSubmit={() => rule('team', team, {}, 'Every game for {match} will be recorded.')} /></div>
              <Button focusKey="rec:team:go" disabled={busy || !team.trim()} onEnter={() => rule('team', team, {}, 'Every game for {match} will be recorded.')} testId="record-team-go">Record games</Button>
            </div>
          </div>
        )}
        <Button focusKey="rec:advanced" ghost icon={advanced ? 'up' : 'chevD'} pressed={advanced} onEnter={() => setAdvanced((v) => !v)} testId="record-advanced">Keyword (advanced)</Button>
        {advanced && (
          <div className="rec-option" data-testid="record-keyword">
            <span className="label">Record every programme, on any channel, whose title contains</span>
            <div className="flex gap-2 flex-wrap items-center">
              <div className="flex-1" style={{ minWidth: '10rem' }}><FocusableInput focusKey="rec:keyword" label="Keyword" placeholder="A word or phrase" maxLength={80} value={keyword} onChange={setKeyword} onSubmit={() => rule('keyword', keyword, {}, 'Programmes with “{match}” in the title will be recorded.')} /></div>
              <Button focusKey="rec:keyword:go" disabled={busy || !keyword.trim()} onEnter={() => rule('keyword', keyword, {}, 'Programmes with “{match}” in the title will be recorded.')} testId="record-keyword-go">Record matches</Button>
            </div>
          </div>
        )}
        <Button focusKey="rec:close" ghost onEnter={onClose}>Close</Button>
      </FocusGroup>
    </Modal>
  )
}
