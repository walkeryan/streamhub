import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useDecideRequest, useRequests } from '../api/hooks'
import type { MediaRequest } from '../api/types'
import { Button, Chip, Empty, ErrorState, Modal, Skeleton } from '../components/ui'
import { SafeImg } from '../components/SafeImg'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { useGo, useRestoreFocus } from '../nav/back'
import { doesFocusableExist, getCurrentFocusKey, setFocus } from '../nav/spatial'
import { requestStateLabel } from '../lib/requests'
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
  const list = active.data?.requests ?? []

  return (
    <div className="page-pad flex flex-col gap-6" data-testid="requests">
      <h1 className="display">Requests</h1>
      <FocusGroup focusKey="req:tabs" className="flex gap-2 flex-wrap" role="tablist">
        <Chip focusKey="req:tab:mine" pressed={scope === 'mine'} onEnter={() => setSp({}, { replace: true })} testId="requests-tab-mine">My requests</Chip>
        {canApprove && <Chip focusKey="req:tab:all" pressed={scope === 'all'} onEnter={() => setSp({ scope: 'all' }, { replace: true })} testId="requests-tab-all">All requests</Chip>}
      </FocusGroup>
      {active.error ? <ErrorState error={active.error} onRetry={() => void active.refetch()} /> : !active.data ? <Skeleton style={{ height: '12rem' }} /> : !list.length ? (
        <Empty title={scope === 'all' ? 'No requests' : 'You have not requested anything'} hint="Titles that none of your sources have show a Request button on their page." >
          <Button focusKey="req:search" icon="search" onEnter={() => go('/search')}>Search for a title</Button>
        </Empty>
      ) : (
        <FocusGroup focusKey="req:list" className="flex flex-col gap-2" style={{ maxWidth: '60rem' }}>
          {list.map((r) => (
            <div key={r.id} className="panel flex items-center gap-4 flex-wrap" data-testid="request-row" data-state={r.state}>
              <div style={{ flex: 'none', width: '3rem', height: '4.5rem', borderRadius: 'var(--radius-sm)', overflow: 'hidden', background: 'var(--t-surface-alt)' }}>
                <SafeImg src={r.poster} alt="" loading="lazy" style={{ width: '100%', height: '100%', objectFit: 'cover' }} fallback={null} />
              </div>
              <div className="flex-1 min-w-0 flex flex-col gap-1">
                <span className="truncate" style={{ fontWeight: 600 }}>{r.title}{r.year ? ` (${r.year})` : ''}</span>
                <span className="label">{[requestStateLabel(r.state), seasonsLabel(r), scope === 'all' && r.profile_name ? `Requested by ${r.profile_name}` : null, new Date(r.created_at).toLocaleDateString()].filter(Boolean).join(' · ')}</span>
                {r.note && <span className="muted">{r.note}</span>}
              </div>
              <Button focusKey={`req:${r.id}:open`} icon="info" label={`Details: ${r.title}`} onEnter={() => go(`/title/${encodeURIComponent(r.title_id)}`)}>Details</Button>
              {scope === 'all' && r.state === 'pending_approval' && <>
                <Button focusKey={`req:${r.id}:approve`} primary icon="check" label={`Approve: ${r.title}`} onEnter={() => run(r, true)} testId="request-approve">Approve</Button>
                <Button focusKey={`req:${r.id}:decline`} danger icon="x" label={`Decline: ${r.title}`} onEnter={() => { setNote(''); setDeclining(r) }} testId="request-decline">Decline</Button>
              </>}
            </div>
          ))}
        </FocusGroup>
      )}
      <Modal open={!!declining} onClose={() => setDeclining(null)} title={`Decline ${declining?.title ?? ''}?`} firstFocusKey="reqdecline:cancel">
        <p className="muted">{declining?.profile_name ? `${declining.profile_name} is told, with your note if you add one.` : 'The requester is told, with your note if you add one.'}</p>
        <FocusableInput focusKey="reqdecline:note" label="Note for the requester (optional)" placeholder="Note for the requester (optional)" maxLength={500} value={note} onChange={setNote} onSubmit={confirmDecline} />
        <div className="flex gap-3">
          <Button focusKey="reqdecline:cancel" onEnter={() => setDeclining(null)}>Keep request</Button>
          <Button focusKey="reqdecline:ok" danger icon="x" onEnter={confirmDecline}>Decline request</Button>
        </div>
      </Modal>
    </div>
  )
}
