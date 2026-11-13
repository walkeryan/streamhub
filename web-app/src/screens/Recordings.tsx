import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useDeleteRecording, useDeleteRecordingRule, usePatchRecordingRule, useRecordingRules, useRecordings } from '../api/hooks'
import type { Recording, RecordingRule } from '../api/types'
import { Button, Chip, Empty, ErrorState, Modal, Skeleton } from '../components/ui'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { useGo, useRestoreFocus } from '../nav/back'
import { useHistoryState } from '../nav/historyState'
import { doesFocusableExist, getCurrentFocusKey, setFocus } from '../nav/spatial'
import { RecordingBadge } from '../recordings/RecordingBadge'
import { useCan } from '../api/me'
import { RULE_KIND_LABEL, episodeLabel, recordingSize } from '../lib/recordings'
import { fmtTime } from '../lib/util'
import { useUi } from '../store/ui'

type Tab = 'upcoming' | 'recorded' | 'rules'
const TABS: Array<{ id: Tab; label: string }> = [{ id: 'upcoming', label: 'Upcoming' }, { id: 'recorded', label: 'Recorded' }, { id: 'rules', label: 'Rules' }]
const KEEP = [null, 3, 5, 10, 20]

const day = (iso: string) => {
  const d = new Date(iso), today = new Date()
  const days = Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() - new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime()) / 86_400_000)
  return days === 0 ? 'Today' : days === 1 ? 'Tomorrow' : days === -1 ? 'Yesterday' : d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })
}
const when = (r: Recording) => `${day(r.starts_at)} · ${fmtTime(r.starts_at)} to ${fmtTime(r.ends_at)}`

export function Recordings() {
  const [sp] = useSearchParams()
  // ?tab= opens on a tab; changing tabs stays on this history entry, so Back and focus memory are undisturbed.
  const [tab, setTab] = useHistoryState<Tab>('rec:tab', TABS.find((t) => t.id === sp.get('tab'))?.id ?? 'upcoming')
  const allowed = useCan('recordings')
  const isAdmin = useCan('settings.admin')
  const list = useRecordings(allowed)
  const rules = useRecordingRules(allowed && !!list.data?.enabled)
  const remove = useDeleteRecording()
  const patchRule = usePatchRecordingRule()
  const removeRule = useDeleteRecordingRule()
  const toast = useUi((s) => s.toast)
  const go = useGo()
  const [deleting, setDeleting] = useState<Recording | null>(null)
  const [deletingRule, setDeletingRule] = useState<RecordingRule | null>(null)
  const [editing, setEditing] = useState<RecordingRule | null>(null)
  const [draft, setDraft] = useState({ match: '', new_only: false, keep: null as number | null })
  useRestoreFocus(!allowed || !!list.data || !!list.error, list.data?.enabled ? `rec:tab:${tab}` : 'rec:guide')

  const all = list.data?.recordings ?? []
  const upcoming = all.filter((r) => r.state === 'scheduled' || r.state === 'recording')
  const recorded = all.filter((r) => r.state === 'done' || r.state === 'failed' || r.state === 'missed').reverse()
  const ruleList = rules.data?.rules ?? []
  const mayChange = list.data?.can_schedule !== false
  const shown = tab === 'upcoming' ? upcoming.length : tab === 'recorded' ? recorded.length : ruleList.length
  // A cancelled or deleted row takes the focused button with it: land on the tab instead of nowhere.
  useEffect(() => {
    if (list.data && !document.querySelector('.scrim') && !doesFocusableExist(getCurrentFocusKey())) void setFocus(`rec:tab:${tab}`)
  }, [list.data, rules.data, tab, shown])

  const failed = (e: Error) => toast(e.message, 'error')
  const cancel = (r: Recording) => {
    if (remove.isPending) return
    remove.mutate(r.id, { onSuccess: () => toast(r.state === 'recording' ? `Stopped recording ${r.title}.` : `${r.title} will not be recorded.`, 'success'), onError: failed })
  }
  const confirmDelete = () => {
    const r = deleting
    setDeleting(null)
    if (r) remove.mutate(r.id, { onSuccess: () => toast(`Deleted ${r.title}.`, 'success'), onError: failed })
  }
  const toggleRule = (rule: RecordingRule) => {
    if (patchRule.isPending) return
    patchRule.mutate({ id: rule.id, patch: { enabled: !rule.enabled } }, {
      onSuccess: (r) => toast(r.enabled ? `Rule on: matching programmes are being scheduled.` : `Rule off. Its upcoming recordings were removed.`, 'success'), onError: failed,
    })
  }
  const edit = (rule: RecordingRule) => { setDraft({ match: rule.match, new_only: rule.new_only, keep: rule.keep ?? null }); setEditing(rule) }
  const saveRule = () => {
    const rule = editing
    if (!rule || !draft.match.trim() || patchRule.isPending) return
    patchRule.mutate({ id: rule.id, patch: { match: draft.match.trim(), new_only: draft.new_only, keep: draft.keep } }, { onSuccess: () => { setEditing(null); toast('Rule saved.', 'success') }, onError: failed })
  }
  const confirmDeleteRule = () => {
    const rule = deletingRule
    setDeletingRule(null)
    if (rule) removeRule.mutate(rule.id, { onSuccess: () => toast('Rule deleted.', 'success'), onError: failed })
  }

  let body
  if (!allowed) body = <Empty title="Recordings are not available" hint="This profile cannot use recordings. Ask the person who runs this StreamHub." />
  else if (list.error) body = <ErrorState error={list.error} onRetry={() => void list.refetch()} />
  else if (!list.data) body = <Skeleton style={{ height: '12rem' }} />
  else if (!list.data.enabled) body = isAdmin ? (
    <div className="panel flex flex-col gap-3 items-start" style={{ maxWidth: '44rem' }} role="status" data-testid="recordings-off">
      <h2 className="title-xl">Recording is not set up on this server</h2>
      <p className="muted">Nothing can be recorded until the server has a folder to record into. Set RECORDINGS_DIR to a writable folder on the library disk and restart the server; Record then appears on the guide.</p>
      <Button focusKey="rec:guide" icon="guide" onEnter={() => go('/live?view=guide')}>Open the guide</Button>
    </div>
  ) : <Empty title="Recording is not available" hint="Recording has not been set up on this server." />
  else body = <>
    <FocusGroup focusKey="rec:tabs" className="flex gap-2 flex-wrap" role="tablist">
      {TABS.map((t) => <Chip key={t.id} focusKey={`rec:tab:${t.id}`} pressed={tab === t.id} onEnter={() => setTab(t.id)} testId={`recordings-tab-${t.id}`}>{t.label}</Chip>)}
    </FocusGroup>
    {tab === 'upcoming' && (upcoming.length === 0 ? (
      <Empty title="Nothing is set to record" hint="Choose a programme on the guide, then Record: that programme, every new episode, or every game of a team.">
        <Button focusKey="rec:guide" icon="guide" onEnter={() => go('/live?view=guide')}>Open the guide</Button>
      </Empty>
    ) : (
      <FocusGroup focusKey="rec:list" className="flex flex-col gap-2" style={{ maxWidth: '60rem' }}>
        {upcoming.map((r) => (
          <div key={r.id} className="panel rec-row" data-testid="recording-row" data-state={r.state}>
            <div className="flex-1 min-w-0 flex flex-col gap-1">
              <span className="flex items-center gap-2 min-w-0"><span className="truncate" style={{ fontWeight: 600 }}>{r.title}</span><RecordingBadge state={r.state} /></span>
              <span className="label">{[r.subtitle, episodeLabel(r), r.channel_name, when(r), r.rule_id ? 'From a rule' : null].filter(Boolean).join(' · ')}</span>
            </div>
            {mayChange && <Button focusKey={`rec:${r.id}:cancel`} danger icon="x" label={`${r.state === 'recording' ? 'Stop recording' : 'Cancel'}: ${r.title}`} onEnter={() => cancel(r)} testId="recording-cancel">{r.state === 'recording' ? 'Stop recording' : 'Cancel'}</Button>}
          </div>
        ))}
      </FocusGroup>
    ))}
    {tab === 'recorded' && (recorded.length === 0 ? <Empty title="No recordings yet" hint="Finished recordings are listed here, newest first." /> : (
      <FocusGroup focusKey="rec:list" className="flex flex-col gap-2" style={{ maxWidth: '60rem' }}>
        {recorded.map((r) => (
          <div key={r.id} className="panel rec-row" data-testid="recording-row" data-state={r.state}>
            <div className="flex-1 min-w-0 flex flex-col gap-1">
              <span className="flex items-center gap-2 min-w-0"><span className="truncate" style={{ fontWeight: 600 }}>{r.title}</span>{r.state !== 'done' && <RecordingBadge state={r.state} />}</span>
              <span className="label">{[r.subtitle, episodeLabel(r), r.channel_name, when(r), recordingSize(r.size_bytes)].filter(Boolean).join(' · ')}</span>
              {r.state !== 'done' && <span className="muted" data-testid="recording-reason">{r.error ?? (r.state === 'missed' ? 'No connection was free when it started.' : 'The recording did not finish.')}</span>}
            </div>
            {r.state === 'done' && <Button focusKey={`rec:${r.id}:play`} primary icon="play" label={`Play: ${r.title}`} onEnter={() => go(`/play/${encodeURIComponent(`rec:${r.id}`)}`)} testId="recording-play">Play</Button>}
            {mayChange && <Button focusKey={`rec:${r.id}:delete`} danger icon="trash" label={`${r.state === 'done' ? 'Delete' : 'Remove'}: ${r.title}`} onEnter={() => setDeleting(r)} testId="recording-delete">{r.state === 'done' ? 'Delete' : 'Remove'}</Button>}
          </div>
        ))}
      </FocusGroup>
    ))}
    {tab === 'rules' && (rules.error ? <ErrorState error={rules.error} onRetry={() => void rules.refetch()} /> : !rules.data ? <Skeleton style={{ height: '12rem' }} /> : ruleList.length === 0 ? (
      <Empty title="No recording rules" hint="A rule records every new episode of a series, every game of a team, or every programme with a word in its title. Make one from a programme on the guide.">
        <Button focusKey="rec:guide" icon="guide" onEnter={() => go('/live?view=guide')}>Open the guide</Button>
      </Empty>
    ) : (
      <FocusGroup focusKey="rec:list" className="flex flex-col gap-2" style={{ maxWidth: '60rem' }}>
        {ruleList.map((rule) => (
          <div key={rule.id} className="panel rec-row" data-testid="rule-row" data-enabled={rule.enabled}>
            <div className="flex-1 min-w-0 flex flex-col gap-1">
              <span className="truncate" style={{ fontWeight: 600 }}>{rule.match}</span>
              <span className="label">{[RULE_KIND_LABEL[rule.kind], rule.channel_name ?? 'Any channel', rule.new_only ? 'New only' : 'Repeats too', rule.keep ? `Keeps the last ${rule.keep}` : 'Keeps all', rule.enabled ? null : 'Off'].filter(Boolean).join(' · ')}</span>
            </div>
            {mayChange && <>
              <Chip focusKey={`rule:${rule.id}:on`} pressed={rule.enabled} label={`${rule.enabled ? 'On' : 'Off'}: ${rule.match}`} onEnter={() => toggleRule(rule)} testId="rule-toggle">{rule.enabled ? 'On' : 'Off'}</Chip>
              <Button focusKey={`rule:${rule.id}:edit`} label={`Edit: ${rule.match}`} onEnter={() => edit(rule)} testId="rule-edit">Edit</Button>
              <Button focusKey={`rule:${rule.id}:delete`} danger icon="trash" label={`Delete: ${rule.match}`} onEnter={() => setDeletingRule(rule)} testId="rule-delete">Delete</Button>
            </>}
          </div>
        ))}
      </FocusGroup>
    ))}
  </>

  return (
    <div className="page-pad flex flex-col gap-6" data-testid="recordings">
      <h1 className="display">Recordings</h1>
      {body}
      <Modal open={!!deleting} onClose={() => setDeleting(null)} title={`${deleting?.state === 'done' ? 'Delete' : 'Remove'} ${deleting?.title ?? ''}?`} firstFocusKey="recdel:cancel" testId="recording-delete-confirm">
        <p className="muted">{deleting?.state === 'done' ? 'The recording and its file are deleted from the server. This cannot be undone.' : 'It is taken off this list. Nothing was recorded.'}</p>
        <div className="flex gap-3 flex-wrap">
          <Button focusKey="recdel:cancel" onEnter={() => setDeleting(null)}>Keep</Button>
          <Button focusKey="recdel:ok" danger icon="trash" onEnter={confirmDelete} testId="recording-delete-go">{deleting?.state === 'done' ? 'Delete recording' : 'Remove'}</Button>
        </div>
      </Modal>
      <Modal open={!!deletingRule} onClose={() => setDeletingRule(null)} title={`Delete the rule for ${deletingRule?.match ?? ''}?`} firstFocusKey="ruledel:cancel" testId="rule-delete-confirm">
        <p className="muted">Deleting a rule also removes the upcoming recordings it scheduled. Recordings that are already finished stay.</p>
        <div className="flex gap-3 flex-wrap">
          <Button focusKey="ruledel:cancel" onEnter={() => setDeletingRule(null)}>Keep rule</Button>
          <Button focusKey="ruledel:ok" danger icon="trash" onEnter={confirmDeleteRule} testId="rule-delete-go">Delete rule</Button>
        </div>
      </Modal>
      <Modal open={!!editing} onClose={() => setEditing(null)} title={editing ? `${RULE_KIND_LABEL[editing.kind]} rule` : 'Rule'} firstFocusKey="ruleedit:match" testId="rule-edit-dialog">
        <FocusGroup focusKey="ruleedit" className="flex flex-col gap-3">
          <span className="label">{editing?.kind === 'series' ? 'Series title' : editing?.kind === 'team' ? 'Team' : 'Title contains'}</span>
          <FocusableInput focusKey="ruleedit:match" label="Match" maxLength={80} value={draft.match} onChange={(match) => setDraft((d) => ({ ...d, match }))} onSubmit={saveRule} />
          <div className="flex gap-2 flex-wrap items-center">
            <Chip focusKey="ruleedit:new" pressed={draft.new_only} onEnter={() => setDraft((d) => ({ ...d, new_only: !d.new_only }))}>New only (skip repeats)</Chip>
          </div>
          <span className="label">Keep</span>
          <FocusGroup focusKey="ruleedit:keep" className="flex gap-2 flex-wrap">
            {KEEP.map((n) => <Chip key={n ?? 'all'} focusKey={`ruleedit:keep:${n ?? 'all'}`} pressed={draft.keep === n} onEnter={() => setDraft((d) => ({ ...d, keep: n }))}>{n ? `Last ${n}` : 'All'}</Chip>)}
          </FocusGroup>
          <p className="muted label">Changing what a rule matches removes the upcoming recordings it scheduled and schedules the new matches.</p>
          <div className="flex gap-3 flex-wrap">
            <Button focusKey="ruleedit:save" primary disabled={!draft.match.trim() || patchRule.isPending} onEnter={saveRule} testId="rule-save">Save</Button>
            <Button focusKey="ruleedit:cancel" ghost onEnter={() => setEditing(null)}>Cancel</Button>
          </div>
        </FocusGroup>
      </Modal>
    </div>
  )
}
