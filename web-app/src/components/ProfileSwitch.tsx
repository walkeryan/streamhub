import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { api, unwrap, ApiError } from '../api/client'
import { useAuthCapabilities } from '../api/contentPolicy'
import { useProfiles } from '../api/hooks'
import type { AuthTokens, Profile } from '../api/types'
import { useSession } from '../store/session'
import { OwnerPin } from './OwnerPin'
import { isAuthTokens } from '../api/ownerPin'
import { Button, ErrorState, Modal } from './ui'
import { FocusGroup } from '../nav/Focusable'

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
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [finishing,setFinishing]=useState(false)
  const committing=useRef(false)
  const pending = useRef<AbortController | null>(null)
  const origin = useRef({ profileId: current.id, generation: useSession.getState().generation })
  const selectedId = useRef<string | null>(null)
  useEffect(() => () => pending.current?.abort(), [])
  const cancel = () => { if(committing.current)return;pending.current?.abort(); onClose() }
  const choose = (p: Profile) => { selectedId.current = p.id; pending.current?.abort(); setTarget(p); setError(''); setBusy(false) }
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
    if (!target || busy || current.content_restricted || !current.is_admin || !target.content_restricted || (pending.current && !pending.current.signal.aborted)) return
    const controller = new AbortController(); pending.current?.abort(); pending.current = controller
    setBusy(true); setError('')
    try {
      const tokens = await unwrap<AuthTokens>(api.POST('/auth/profile-switch', { signal: controller.signal, body: { profile_id: target.id } }))
      await apply(tokens, controller.signal, target.id)
    } catch (e) {
      if (!controller.signal.aborted) setError(e instanceof ApiError && e.code === 'owner_pin_required' ? 'The owner PIN is required to exit this profile.' : e instanceof ApiError && e.status === 401 ? 'The existing profile credential was not accepted.' : e instanceof ApiError && e.code === 'profile_switch_locked' ? 'This profile has no usable existing credential. Ask the owner to sign in.' : e instanceof Error ? e.message : 'Could not switch profiles.')
    } finally { if (pending.current === controller) { pending.current = null; if (!controller.signal.aborted) setBusy(false) } }
  }
  const needsOwnerPin = !!target && (current.content_restricted || !current.is_admin || !target.content_restricted)
  return <Modal open onClose={cancel} title="Switch profile" firstFocusKey="switch:cancel" wide>
    <p>Current profile: {current.name}. Viewing history and saved filters stay with each profile.</p>
    {profiles.error ? <ErrorState error={profiles.error} onRetry={() => void profiles.refetch()} /> : <FocusGroup focusKey="switch:profiles" className="flex flex-wrap gap-3">
      {profiles.data?.filter((p) => p.id !== current.id).map((p) => <Button key={p.id} focusKey={`switch:${p.id}`} disabled={busy||finishing} pressed={target?.id === p.id} onEnter={() => choose(p)}>{p.name}</Button>)}
    </FocusGroup>}
    {target && <section className="flex flex-col gap-3"><h3 className="heading">Switch to {target.name}</h3>
      {needsOwnerPin ? <OwnerPin key={target.id} targetId={target.id} targetName={target.name} action="profile_switch" onCommitting={value=>{committing.current=value;setFinishing(value)}} onUncertain={()=>{if(useSession.getState().generation===origin.current.generation){useSession.getState().clear();qc.clear();navigate("/?owner_pin_uncertain=1",{replace:true});onClose()}}} onApproved={async (result, signal) => { if (!isAuthTokens(result)) throw new Error("Owner authorization returned the wrong action."); await apply(result, signal, target.id) }} /> : <p>Your verified owner session authorizes entry into this restricted profile.</p>}
      {!needsOwnerPin && <Button focusKey="switch:confirm" primary disabled={busy} onEnter={() => void submit()}>{busy ? 'Switching…' : `Continue as ${target.name}`}</Button>}
    </section>}
    {error && <p role="alert">{error}</p>}
    <Button focusKey="switch:cancel" disabled={finishing} onEnter={cancel}>Cancel</Button>
  </Modal>
}
