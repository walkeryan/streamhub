import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useCreateRequest, useDecideRequest, useRefreshRequest, useRequests } from '../api/hooks'
import { useCan } from '../api/me'
import type { MediaRequest } from '../api/types'
import { Button, Chip, Empty, ErrorState, GlassIconButton, Modal, PageHeader, Skeleton, nameGradient } from '../ds'
import { RequestStatus } from '../components/RequestStatus'
import { SafeImg } from '../components/SafeImg'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { useGo, useRestoreFocus } from '../nav/back'
import { doesFocusableExist, getCurrentFocusKey, setFocus } from '../nav/spatial'
import { PHASE_LABEL, byActivity } from '../lib/requests'
import { useUi } from '../store/ui'

const seasonsLabel = (r: MediaRequest) =>
  r.media_type !== 'tv' ? null : r.seasons?.length ? `Season${r.seasons.length > 1 ? 's' : ''} ${r.seasons.join(', ')}` : 'All seasons'

export function Requests() {
  const [sp, setSp] = useSearchParams()
  const mine = useRequests('mine')
  // The server decides who may approve; the tab only follows its answer.
  const canApprove = !!mine.data?.can_approve
  const scope = canApprove && sp.get('scope') === 'all' ? 'all' : 'mine'
  const all = useRequests('all', scope === 'all')
  const active = scope === 'all' ? all : mine
  const decide = useDecideRequest()
  const refresh = useRefreshRequest()
  const again = useCreateRequest()
  const mayRequest = useCan('requests')
  const toast = useUi((s) => s.toast)
  const go = useGo()
  const [declining, setDeclining] = useState<MediaRequest | null>(null)
  const [note, setNote] = useState('')
  const decided = useRef<string | null>(null)
  useRestoreFocus(!!mine.data || !!mine.error, 'req:tab:mine')
  // Approve / Decline leave a row once it is decided (here or on another device): fall back to its Details button.
  useEffect(() => {
    if (!active.data || doesFocusableExist(getCurrentFocusKey())) return
    const row = decided.current && `req:${decided.current}:open`
    void setFocus(row && doesFocusableExist(row) ? row : `req:tab:${scope}`)
  }, [active.data, scope])

  const run = (r: MediaRequest, approve: boolean, why?: string) => {
    if (decide.isPending) return
    decided.current = r.id
    decide.mutate({ id: r.id, approve, note: why }, {
      onSuccess: (done) => {
        void setFocus(`req:${r.id}:open`)
        toast(approve ? (done.state === 'sent' ? `Approved ${r.title}.` : `Approved ${r.title}. It will be sent when the request manager answers.`) : `Declined ${r.title}.`, 'success')
      },
      onError: (e) => toast(e.message, 'error'),
    })
  }
  const confirmDecline = () => {
    const r = declining
    setDeclining(null)
    if (r) run(r, false, note)
    setNote('')
  }
  // What the API offers for a request that needs a look: ask the request manager again now, or ask for the title again.
  const check = (r: MediaRequest) => {
    if (refresh.isPending) return
    refresh.mutate(r.id, {
      onSuccess: (done) => toast(`Checked ${r.title}: ${PHASE_LABEL[done.progress.phase].toLowerCase()}.`, done.progress.phase === 'stalled' || done.progress.phase === 'unknown' ? 'info' : 'success'),
      onError: (e) => toast(e.message, 'error'),
    })
  }
  const requestAgain = (r: MediaRequest) => {
    if (again.isPending) return
    decided.current = r.id
    again.mutate({ media_type: r.media_type, tmdb_id: r.tmdb_id, seasons: r.seasons ?? null }, {
      onSuccess: () => toast(`Requested ${r.title} again.`, 'success'),
      onError: (e) => toast(e.message, 'error'),
    })
  }
  // What is downloading comes first, then what is waiting, then what is over; newest first within each.
  const list = byActivity(active.data?.requests ?? [])
  // A request that failed is history once the title has been asked for again.
  const superseded = (r: MediaRequest) => list.some((x) => x.title_id === r.title_id && x.created_at > r.created_at)

  return (
    <div className="page-pad lib-page" data-testid="requests">
      <PageHeader title="Requests" className="lib-head">
        <FocusGroup focusKey="req:tabs" className="lib-chips" role="tablist">
          <Chip focusKey="req:tab:mine" pressed={scope === 'mine'} onEnter={() => setSp({}, { replace: true })} testId="requests-tab-mine">My requests</Chip>
          {canApprove && <Chip focusKey="req:tab:all" pressed={scope === 'all'} onEnter={() => setSp({ scope: 'all' }, { replace: true })} testId="requests-tab-all">All requests</Chip>}
        </FocusGroup>
      </PageHeader>
      {active.error ? <ErrorState error={active.error} onRetry={() => void active.refetch()} /> : !active.data ? (
        <div className="lib-list" aria-hidden="true">{Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="lib-row-skel" />)}</div>
      ) : !list.length ? (
        <Empty icon="download" title={scope === 'all' ? 'No requests' : 'You have not requested anything'} hint="Titles that none of your sources have show a Request button on their page." >
          <Button focusKey="req:search" primary icon="search" onEnter={() => go('/search')}>Search for a title</Button>
        </Empty>
      ) : (
        <FocusGroup focusKey="req:list" className="lib-list">
          {list.map((r) => (
            <div key={r.id} className="ds-panel lib-row req-row" data-testid="request-row" data-state={r.state} data-phase={r.progress.phase}>
              <div className="req-poster">
                <SafeImg src={r.poster} alt="" loading="lazy" fallback={<span className="ds-art-fallback" style={{ background: nameGradient(r.title) }} />} />
              </div>
              <div className="lib-row-copy">
                <span className="lib-row-title">{r.title}{r.year ? ` (${r.year})` : ''}</span>
                <RequestStatus progress={r.progress} of={r.title} />
                <span className="label">{[seasonsLabel(r), scope === 'all' && r.profile_name ? `Requested by ${r.profile_name}` : null, new Date(r.created_at).toLocaleDateString()].filter(Boolean).join(' · ')}</span>
                {/* The status already says a note that is its detail (a decline reason, why it is not sent yet). */}
                {r.note && r.note !== r.progress.detail && <span className="muted">{r.note}</span>}
              </div>
              <div className="lib-row-actions">
                <GlassIconButton focusKey={`req:${r.id}:open`} icon="info" label={`Details: ${r.title}`} tip="Details" onEnter={() => go(`/title/${encodeURIComponent(r.title_id)}`)} />
                {(r.progress.phase === 'stalled' || r.progress.phase === 'unknown' || r.progress.phase === 'approved') && <GlassIconButton focusKey={`req:${r.id}:check`} icon="refresh" label={`Check again: ${r.title}`} tip="Check again" disabled={refresh.isPending} onEnter={() => check(r)} testId="request-check" />}
                {r.progress.phase === 'failed' && mayRequest && !superseded(r) && <GlassIconButton focusKey={`req:${r.id}:again`} icon="plus" label={`Request again: ${r.title}`} tip="Request again" disabled={again.isPending} onEnter={() => requestAgain(r)} testId="request-again" />}
                {scope === 'all' && r.state === 'pending_approval' && <>
                  <Button focusKey={`req:${r.id}:approve`} primary icon="check" label={`Approve: ${r.title}`} onEnter={() => run(r, true)} testId="request-approve">Approve</Button>
                  <Button focusKey={`req:${r.id}:decline`} ghost icon="x" label={`Decline: ${r.title}`} onEnter={() => { setNote(''); setDeclining(r) }} testId="request-decline">Decline</Button>
                </>}
              </div>
            </div>
          ))}
        </FocusGroup>
      )}
      <Modal open={!!declining} onClose={() => setDeclining(null)} title={`Decline ${declining?.title ?? ''}?`} firstFocusKey="reqdecline:cancel">
        <p className="muted">{declining?.profile_name ? `${declining.profile_name} is told, with your note if you add one.` : 'The requester is told, with your note if you add one.'}</p>
        <FocusableInput focusKey="reqdecline:note" label="Note for the requester (optional)" placeholder="Note for the requester (optional)" maxLength={500} value={note} onChange={setNote} onSubmit={confirmDecline} />
        <div className="lib-actions">
          <Button focusKey="reqdecline:cancel" ghost onEnter={() => setDeclining(null)}>Keep request</Button>
          <Button focusKey="reqdecline:ok" danger icon="x" onEnter={confirmDecline}>Decline request</Button>
        </div>
      </Modal>
    </div>
  )
}
