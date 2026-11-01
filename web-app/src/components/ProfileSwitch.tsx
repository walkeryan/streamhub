import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { api, unwrap, ApiError } from '../api/client'
import { useAuthCapabilities } from '../api/contentPolicy'
import { useProfiles } from '../api/hooks'
import type { AuthTokens, Profile } from '../api/types'
import { useSession } from '../store/session'
import { OwnerReauth } from './OwnerReauth'
import { Button, ErrorState, Modal } from './ui'
import { FocusGroup, FocusableInput } from '../nav/Focusable'

export function ProfileSwitch() {
  const capability = useAuthCapabilities()
  const current = useSession((s) => s.profile)!
  const clear = useSession((s) => s.clear)
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  if (!capability.data?.content_policy_enabled) return <Button focusKey="prof:switch" icon="user" disabled={capability.isPending || !!capability.error} onEnter={() => { clear(); qc.clear() }} testId="switch-profile">Sign out to switch profile</Button>
  return <><Button focusKey="prof:switch" icon="user" onEnter={() => setOpen(true)} testId="switch-profile">Switch profile</Button>
    {!current.content_restricted && <Button focusKey="prof:signout" onEnter={() => { clear(); qc.clear() }}>Sign out</Button>}
    {open && <SwitchDialog key={current.id} onClose={() => setOpen(false)} />}
  </>
}

function SwitchDialog({ onClose }: { onClose: () => void }) {
  const current = useSession((s) => s.profile)!
  const setSession = useSession((s) => s.setSession)
  const profiles = useProfiles()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [target, setTarget] = useState<Profile | null>(null)
  const [secret, setSecret] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const pending = useRef<AbortController | null>(null)
  const origin = useRef({ profileId: current.id, generation: useSession.getState().generation })
  const selectedId = useRef<string | null>(null)
  useEffect(() => () => pending.current?.abort(), [])
  const cancel = () => { pending.current?.abort(); setSecret(''); onClose() }
  const choose = (p: Profile) => { selectedId.current = p.id; pending.current?.abort(); setTarget(p); setSecret(''); setError(''); setBusy(false) }
  const apply = async (tokens: AuthTokens, signal: AbortSignal, expectedTarget: string) => {
    const stillCurrent = () => { const s = useSession.getState(); return !signal.aborted && s.profile?.id === origin.current.profileId && s.generation === origin.current.generation && selectedId.current === expectedTarget }
    if (!stillCurrent()) { if (!signal.aborted) throw new Error('Sign-in changed. Close this dialog and try again.'); return }
    if (tokens.profile.id !== expectedTarget) throw new Error('The server returned a different profile. No switch was applied.')
    await qc.cancelQueries()
    if (!stillCurrent()) { if (!signal.aborted) throw new Error('Sign-in changed. Close this dialog and try again.'); return }
    qc.clear(); setSession(tokens.access_token, tokens.refresh_token, tokens.profile)
    navigate('/', { replace: true }); onClose()
  }
  const submit = async () => {
    if (!target || busy || current.content_restricted || (pending.current && !pending.current.signal.aborted)) return
    const controller = new AbortController(); pending.current?.abort(); pending.current = controller
    setBusy(true); setError('')
    try {
      const tokens = await unwrap<AuthTokens>(api.POST('/auth/profile-switch', { signal: controller.signal, body: { profile_id: target.id, ...(!current.is_admin && secret ? target.has_password ? { password: secret } : { pin: secret } : {}) } }))
      await apply(tokens, controller.signal, target.id)
    } catch (e) {
      if (!controller.signal.aborted) setError(e instanceof ApiError && e.code === 'owner_reauth_required' ? 'The owner must sign in again to exit this profile.' : e instanceof ApiError && e.status === 401 ? 'The existing profile credential was not accepted.' : e instanceof ApiError && e.code === 'profile_switch_locked' ? 'This profile has no usable existing credential. Ask the owner to sign in.' : e instanceof Error ? e.message : 'Could not switch profiles.')
    } finally { if (pending.current === controller) { pending.current = null; if (!controller.signal.aborted) setBusy(false) } }
  }
  const credentialNeeded = !!target && !current.is_admin && !current.content_restricted
  const locked = credentialNeeded && !target.has_pin && !target.has_password
  return <Modal open onClose={cancel} title="Switch profile" firstFocusKey="switch:cancel" wide>
    <p>Current profile: {current.name}. Viewing history and saved filters stay with each profile.</p>
    {profiles.error ? <ErrorState error={profiles.error} onRetry={() => void profiles.refetch()} /> : <FocusGroup focusKey="switch:profiles" className="flex flex-wrap gap-3">
      {profiles.data?.filter((p) => p.id !== current.id).map((p) => <Button key={p.id} focusKey={`switch:${p.id}`} disabled={busy} pressed={target?.id === p.id} onEnter={() => choose(p)}>{p.name}</Button>)}
    </FocusGroup>}
    {target && <section className="flex flex-col gap-3"><h3 className="heading">Switch to {target.name}</h3>
      {current.content_restricted ? <OwnerReauth key={target.id} targetId={target.id} targetName={target.name} onApproved={(tokens, signal) => apply(tokens, signal, target.id)} /> : current.is_admin ? <p>You are authorizing this switch as the owner. Returning to an owner profile may require signing in again.</p> : locked ? <p role="alert">This profile has no usable existing credential. Cancel and use Sign out so the owner can sign in. No new PIN can be created here.</p> : <FocusableInput focusKey="switch:credential" type="password" inputMode={target.has_password ? 'text' : 'numeric'} label={target.has_password ? 'Existing profile password' : 'Existing profile PIN'} value={secret} onChange={setSecret} onSubmit={() => void submit()} />}
      {!current.content_restricted && <Button focusKey="switch:confirm" primary disabled={busy || locked || (credentialNeeded && !secret)} onEnter={() => void submit()}>{busy ? 'Switching…' : `Continue as ${target.name}`}</Button>}
    </section>}
    {error && <p role="alert">{error}</p>}
    <Button focusKey="switch:cancel" onEnter={cancel}>Cancel</Button>
  </Modal>
}
