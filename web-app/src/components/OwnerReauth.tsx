import { useEffect, useRef, useState } from 'react'
import { api, unwrap } from '../api/client'
import type { AuthTokens } from '../api/types'
import { Button } from './ui'

type Challenge = { challenge_id: string; poll_token: string; verification_uri: string; expires_in: number; interval: number }
/** Challenge credentials live only in memory. Closing cancels and cannot apply a late approval. */
export function OwnerReauth({ targetId, targetName, onApproved }: { targetId: string; targetName: string; onApproved: (tokens: AuthTokens, signal: AbortSignal) => Promise<void> }) {
  const [challenge, setChallenge] = useState<Challenge | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const live = useRef(true)
  const starting = useRef(false)
  const current = useRef<Challenge | null>(null)
  const controller = useRef<AbortController | null>(null)
  const approve = useRef(onApproved); approve.current = onApproved
  const cancelChallenge = (value: Challenge) => { void api.POST('/auth/owner-reauth/cancel', { body: { challenge_id: value.challenge_id, poll_token: value.poll_token } }).catch(() => { /* expiration is the fallback; never apply canceled approval */ }) }
  useEffect(() => {
    live.current = true
    return () => { live.current = false; controller.current?.abort(); if (current.current) cancelChallenge(current.current); current.current = null }
  }, [])
  const start = async () => {
    if (starting.current) return
    starting.current = true
    setBusy(true); setError('')
    try {
      const value = await unwrap<Challenge>(api.POST('/auth/owner-reauth/start', { body: { profile_id: targetId } }))
      if (!live.current) { cancelChallenge(value); return }
      const uri = new URL(value.verification_uri, window.location.origin)
      if (uri.origin !== window.location.origin || !['http:', 'https:'].includes(uri.protocol)) { cancelChallenge(value); throw new Error('Owner verification must use this Streamhub server.') }
      current.current = value; setChallenge(value)
    } catch (e) { if (live.current) setError(e instanceof Error ? e.message : 'Could not start owner sign-in.') }
    finally { starting.current = false; if (live.current) setBusy(false) }
  }
  useEffect(() => {
    if (!challenge) return
    const abort = new AbortController(); controller.current = abort
    let timer = 0
    const expiry = Date.now() + Math.max(1, challenge.expires_in) * 1000
    const poll = async () => {
      if (abort.signal.aborted || !live.current) return
      if (Date.now() >= expiry) { setError('Owner sign-in expired. Start again.'); setChallenge(null); cancelChallenge(challenge); return }
      try {
        const result = await api.POST('/auth/owner-reauth/poll', { signal: abort.signal, body: { challenge_id: challenge.challenge_id, poll_token: challenge.poll_token } })
        if (abort.signal.aborted || !live.current) return
        if (result.response.status === 428) { timer = window.setTimeout(() => void poll(), Math.max(1, challenge.interval) * 1000); return }
        const tokens = await unwrap<AuthTokens>(Promise.resolve(result))
        current.current = null
        await approve.current(tokens, abort.signal)
      } catch (e) {
        if (!abort.signal.aborted && live.current) { setError(e instanceof Error ? e.message : 'Owner sign-in was denied or expired.'); setChallenge(null); cancelChallenge(challenge) }
      }
    }
    timer = window.setTimeout(() => void poll(), Math.max(1, challenge.interval) * 1000)
    return () => { abort.abort(); window.clearTimeout(timer) }
  }, [challenge])
  return <div className="flex flex-col gap-3">
    <p>The owner must sign in with their authorized Google account to switch to {targetName}. The approval is limited to this profile switch.</p>
    <Button focusKey="switch:reauth-start" primary disabled={busy} onEnter={() => { if (challenge) window.open(challenge.verification_uri, '_blank', 'noopener,noreferrer'); else void start() }}>
      {busy ? 'Preparing owner sign-in…' : challenge ? 'Open owner sign-in' : 'Request owner sign-in'}
    </Button>
    {challenge && <p role="status">Waiting for the owner’s approval. Complete sign-in in the new tab, then return here. Cancel stops this request.</p>}
    {error && <p role="alert">{error}</p>}
  </div>
}
