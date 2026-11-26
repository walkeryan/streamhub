import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '../api/client'
import { useScheduleRecording } from '../api/hooks'
import type { components } from '../api/schema'
import type { Recording } from '../api/types'
import { Button, Chip, ErrorState, Modal } from '../ds'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'

type Plan = components['schemas']['RecordingPlan']
type Airing = components['schemas']['DvrAiring']
type Target = Pick<Airing, 'programme_id' | 'channel_id' | 'start'>
const gb = (value: number) => `${(value / 1024 ** 3).toLocaleString(undefined, { maximumFractionDigits: 1 })} GiB`
const stamp = (value: string) => new Date(value).toLocaleString(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit' })
export function captureTime(start: string, stop: string) {
  const left = new Date(start), right = new Date(stop)
  const tz = left.getTimezoneOffset() !== right.getTimezoneOffset()
  const time = (d: Date) => d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit', ...(tz ? { timeZoneName: 'shortOffset' } : {}) })
  const date = left.toDateString() === right.toDateString() ? '' : right.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' }) + ' '
  return `${time(left)} to ${date}${time(right)}`
}

function Space({ plan }: { plan: Plan }) {
  return <div className="label">
    {plan.free_bytes != null && <p>{gb(plan.free_bytes)} free · {gb(plan.reserve_bytes)} kept in reserve</p>}
    {plan.estimated_bytes != null && <p>Estimated capture · {gb(plan.estimated_bytes)}{plan.free_bytes != null && plan.estimated_bytes + plan.reserve_bytes > plan.free_bytes ? ' · may exceed available space' : ''}</p>}
    {!!plan.evictions.length && <p>When this capture finishes, the keep limit would remove {plan.evictions.join(', ')}.</p>}
  </div>
}
function Priority({ scope, value, set }: { scope: string; value: number; set: (n: number) => void }) {
  return <div role="group" aria-label="Recording priority" className="lib-chips">{(['Low', 'Normal', 'High'] as const).map((label, i) =>
    <Chip key={label} focusKey={`${scope}:priority:${i}`} pressed={value === i - 1} onEnter={() => set(i - 1)}>{label} priority</Chip>)}</div>
}
export function DvrSchedule({ target, focusKey, testId, label, primary, disabled, onSuccess, onError }: {
  target: Target; focusKey: string; testId: string; label: string; primary?: boolean; disabled: boolean
  onSuccess: (r: Recording) => void; onError: (e: Error) => void
}) {
  const profile = useSession(s => s.profile?.id)
  const [priority, setPriority] = useState(0)
  const [choices, setChoices] = useState(false)
  const schedule = useScheduleRecording()
  const preview = useQuery({ queryKey: ['recording-preview', profile, target], retry: false,
    queryFn: ({ signal }) => unwrap<Plan>(api.POST('/recordings/preview', { signal, body: target })) })
  const plan = preview.data
  const save = (chosen: Target, conflicts: number) => schedule.mutate({ ...chosen, priority, confirmed_conflicts: conflicts }, {
    onSuccess, onError: (e) => { onError(e); void preview.refetch() },
  })
  const conflict = !!plan?.conflict_count
  const busy = disabled || schedule.isPending || !!preview.error
  const keep = async () => {
    if (plan) { save(target, plan.conflict_count); return }
    const loaded = (await preview.refetch({ cancelRefetch: false })).data
    if (loaded && loaded.conflict_count === 0) save(target, 0)
  }
  return <>
    {preview.error && <ErrorState error={preview.error} onRetry={() => void preview.refetch()} />}
    {conflict && <p role="status">Tuner conflict · {plan!.source_name ?? 'This source'} supports {plan!.connection_limit} simultaneous {plan!.connection_limit === 1 ? 'connection' : 'connections'}. A lower-priority capture may start late.{plan!.overlapping_titles.length ? ` Overlaps ${plan!.overlapping_titles.join(', ')}.` : ''}</p>}
    {conflict && <Priority scope={focusKey} value={priority} set={setPriority} />}
    {conflict && plan!.alternates.map(a => <Button key={a.programme_id + a.channel_id} focusKey={`${focusKey}:alt:${a.channel_id}:${a.programme_id}`}
      disabled={busy} onEnter={() => save(a, 0)}>Record {stamp(a.start)} on {a.channel_name}</Button>)}
    <Button focusKey={focusKey} primary={primary} icon="record" disabled={busy} onEnter={() => void keep()} testId={testId}>{conflict ? 'Keep this airing' : label}</Button>
    {plan && <Button focusKey={`${focusKey}:details`} ghost pressed={choices} onEnter={() => setChoices(!choices)}>Recording details</Button>}
    {choices && plan && <><Space plan={plan} />{!conflict && <Priority scope={focusKey} value={priority} set={setPriority} />}</>}
  </>
}

export function DvrReview({ row, onClose }: { row: Recording; onClose: () => void }) {
  const profile = useSession(s => s.profile?.id)
  const qc = useQueryClient()
  const toast = useUi(s => s.toast)
  const plan = useQuery({ queryKey: ['recording-plan', profile, row.id], queryFn: ({ signal }) => unwrap<Plan>(api.GET('/recordings/{id}/plan', { signal, params: { path: { id: row.id } } })) })
  const change = useMutation({ mutationFn: (body: components['schemas']['RecordingChange']) => unwrap<Recording>(api.PATCH('/recordings/{id}', { params: { path: { id: row.id } }, body })),
    onSuccess: async () => { await Promise.all([['recordings'], ['recording-plan'], ['recording-preview'], ['epg'], ['channels']].map(queryKey => qc.invalidateQueries({ queryKey }))); toast('Recording updated.', 'success'); onClose() } })
  return <Modal open onClose={onClose} title={row.title} firstFocusKey="dvr:close" variant="sheet">
    <p>{row.channel_name} · {stamp(row.starts_at)} · {captureTime(row.starts_at, row.ends_at)}</p>
    {row.state === 'failed' && row.has_file && <p>{row.captured_from && row.captured_until ? `Partial recording available from ${captureTime(row.captured_from, row.captured_until)}.` : 'A partial recording is available.'}</p>}
    {row.state === 'failed' && row.has_file && row.captured_from && row.captured_until && <p>{Math.ceil((Math.max(0, Date.parse(row.captured_from) - Date.parse(row.starts_at)) + Math.max(0, Date.parse(row.ends_at) - Date.parse(row.captured_until))) / 60000)} programme minutes missed.</p>}
    {row.recovered_at && row.captured_from && <p>Full rerun captured {stamp(row.captured_from)}.</p>}
    {row.guide_review && <p role="status">The guide changed. Choose an airing or keep the original time before recording starts.</p>}
    {plan.error && <ErrorState error={plan.error} onRetry={() => void plan.refetch()} />}
    {plan.data && <><Space plan={plan.data} />
      {!!plan.data.conflict_count && <p role="status">Tuner conflict · {plan.data.source_name ?? 'This source'} supports {plan.data.connection_limit} connections.</p>}
      {(row.guide_review || plan.data.conflict_count > 0) && plan.data.alternates.map(a => <Button key={a.programme_id} focusKey={`dvr:air:${a.programme_id}`} disabled={change.isPending} onEnter={() => change.mutate({ airing: a })}>{stamp(a.start)} · {a.channel_name} · {a.title}</Button>)}
    </>}
    {row.guide_review && <Button focusKey="dvr:keep-time" disabled={change.isPending} onEnter={() => change.mutate({ keep_original_time: true })}>Keep original time</Button>}
    {row.state === 'scheduled' && <Priority scope="dvr" value={row.priority ?? 0} set={priority => { if (!change.isPending) change.mutate({ priority }) }} />}
    {row.has_file && <Button focusKey="dvr:protect" pressed={!!row.protected} disabled={change.isPending} onEnter={() => change.mutate({ protected: !row.protected })}>{row.protected ? 'Release keep protection' : 'Protect from automatic deletion'}</Button>}
    {change.error && <p role="alert">{change.error.message}</p>}
    <Button focusKey="dvr:close" onEnter={onClose}>Close</Button>
  </Modal>
}
