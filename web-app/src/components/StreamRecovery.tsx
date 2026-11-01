import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '../api/client'
import type { components } from '../api/schema'
import { useSession } from '../store/session'
import { Button, Modal } from './ui'

type Status = components['schemas']['PlaybackRecoveryStatus']
type Result = components['schemas']['PlaybackRecoveryResult']
type Action = 'stop' | 'resume'

/** Bound the UI wait even if an adapter ignores abort; outcome may be uncertain. */
function bounded<T>(run: (signal: AbortSignal) => Promise<T>, ms: number, outer?: AbortSignal): Promise<T> {
  const controller = new AbortController()
  const abort = () => controller.abort()
  if (outer?.aborted) abort()
  outer?.addEventListener('abort', abort, { once: true })
  let timer: ReturnType<typeof setTimeout>
  const timeout = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => { controller.abort(); reject(new Error('Operation timed out')) }, ms)
  })
  return Promise.race([run(controller.signal), timeout]).finally(() => {
    clearTimeout(timer)
    outer?.removeEventListener('abort', abort)
  })
}

export function StreamRecovery() {
  const profile = useSession((s) => s.profile)
  return profile?.is_admin ? <AdminStreamRecovery key={profile.id} profileId={profile.id} /> : null
}

function AdminStreamRecovery({ profileId }: { profileId: string }) {
  const qc = useQueryClient()
  const queryKey = ['playback-recovery', profileId] as const
  const status = useQuery({
    queryKey,
    queryFn: ({ signal }) => bounded((requestSignal) => unwrap<Status>(api.GET('/playback/recovery', { signal: requestSignal })), 10_000, signal),
    staleTime: 0,
    retry: false,
    refetchInterval: 10_000,
  })
  const [action, setAction] = useState<Action | null>(null)
  const [checking, setChecking] = useState(false)
  const [snapshot, setSnapshot] = useState<Status | null>(null)
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState<{ text: string; error: boolean } | null>(null)
  const serial = useRef(0)
  const mutation = useRef(false)
  const stillAdmin = () => {
    const p = useSession.getState().profile
    return p?.id === profileId && p.is_admin
  }

  const close = () => {
    if (mutation.current) return
    serial.current++
    setAction(null)
    setSnapshot(null)
  }
  const open = async (next: Action) => {
    if (mutation.current || !stillAdmin()) return
    const request = ++serial.current
    setAction(next)
    setSnapshot(null)
    setChecking(true)
    setFeedback(null)
    const fresh = await status.refetch()
    if (request !== serial.current || !stillAdmin()) return
    setChecking(false)
    if (fresh.isError || !fresh.data) {
      setFeedback({ text: 'Could not verify current sessions. Close this dialog and refresh status before trying again.', error: true })
    } else setSnapshot(fresh.data)
  }
  const confirm = async () => {
    if (!action || !snapshot || checking || mutation.current || !stillAdmin()) return
    mutation.current = true
    setBusy(true)
    setFeedback(null)
    try {
      // Prevent an earlier status response from replacing the mutation result.
      await qc.cancelQueries({ queryKey, exact: true })
      if (!stillAdmin()) return
      if (action === 'stop') {
        const stopped = await bounded((signal) => unwrap<Result>(api.POST('/playback/recovery/stop', { body: { confirm: true }, signal })), 15_000)
        if (!stillAdmin()) return
        qc.setQueryData(queryKey, stopped)
        const partial = stopped.failed > 0 || stopped.cleanup_failed > 0
        setFeedback({
          text: partial
            ? `Playback is paused. ${stopped.stopped} sessions stopped; ${stopped.failed} stop failures and ${stopped.cleanup_failed} cleanup failures remain. Review status and retry Stop all streams.`
            : `Stopped ${stopped.stopped} Streamhub-owned media sessions/connections. New playback is paused until an administrator resumes it.`,
          error: partial,
        })
      } else {
        const resumed = await bounded((signal) => unwrap<Status>(api.POST('/playback/recovery/resume', { body: { confirm: true }, signal })), 15_000)
        if (!stillAdmin()) return
        qc.setQueryData(queryKey, resumed)
        setFeedback({ text: resumed.paused ? 'Playback is still paused. Refresh status before trying again.' : 'New playback is allowed. Viewers can choose a title or channel again.', error: resumed.paused })
      }
      setAction(null)
      setSnapshot(null)
    } catch {
      if (stillAdmin()) {
        setFeedback({ text: 'Could not confirm the operation completed. Refresh status before retrying; do not assume streams stopped or playback resumed.', error: true })
        setAction(null)
        setSnapshot(null)
        void status.refetch()
      }
    } finally {
      mutation.current = false
      setBusy(false)
    }
  }

  return <section className="panel flex flex-col gap-3" data-testid="stream-recovery">
    <h2 className="heading">Stream recovery</h2>
    <p>Stop Streamhub-owned media sessions/connections if playback gets stuck. This interrupts other Streamhub viewers and pauses new playback until an administrator resumes it.</p>
    <p className="muted">This cannot stop direct streams or sessions in external IPTV apps. One shared connection can serve several viewers.</p>
    <p role="status" aria-live="polite">
      {status.isError ? 'Session status unavailable.' : status.data ? `${status.data.active_sessions} Streamhub-owned media sessions/connections · ${status.data.paused ? 'New playback paused' : 'New playback allowed'}` : 'Checking sessions…'}
    </p>
    {feedback && <p role={feedback.error ? 'alert' : 'status'}>{feedback.text}</p>}
    <div className="flex gap-3 flex-wrap">
      <Button focusKey="recovery:refresh" disabled={busy} onEnter={() => void status.refetch()}>Refresh status</Button>
      <Button focusKey="recovery:stop" danger disabled={busy} onEnter={() => void open('stop')}>Stop all streams</Button>
      {status.data?.paused && <Button focusKey="recovery:resume" disabled={busy || status.data.active_sessions > 0} onEnter={() => void open('resume')}>Resume new playback</Button>}
    </div>
    <Modal open={action !== null} onClose={close} title={action === 'resume' ? 'Resume new playback?' : 'Stop all streams?'} firstFocusKey="recovery:cancel">
      <div className="flex flex-col gap-3">
        <p>{action === 'resume' ? 'Allow viewers to choose Play or Try again. Previously stopped stream URLs remain invalid. Older web players may need to reload first.' : 'This interrupts other Streamhub viewers. New playback will stay paused until an administrator chooses Resume new playback.'}</p>
        <p>Direct streams and external IPTV apps are not controlled by Streamhub.</p>
        <p role="status" aria-live="polite">{checking ? 'Refreshing the affected session count…' : snapshot ? `${snapshot.active_sessions} Streamhub-owned media sessions/connections at the latest check. This count can change before confirmation.` : 'Current session count could not be verified.'}</p>
        {feedback?.error && <p role="alert">{feedback.text}</p>}
        <div className="flex gap-3 flex-wrap">
          <Button focusKey="recovery:cancel" disabled={busy} onEnter={close}>Cancel</Button>
          <Button focusKey="recovery:confirm" danger={action === 'stop'} disabled={busy || checking || !snapshot} onEnter={() => void confirm()}>{busy ? 'Working…' : action === 'resume' ? 'Confirm resume' : 'Confirm stop all streams'}</Button>
        </div>
      </div>
    </Modal>
  </section>
}
