import { useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { OwnerPin } from './OwnerPin'
import type { OwnerAction } from '../api/ownerPin'
import { api, unwrap } from '../api/client'
import { useProfiles } from '../api/hooks'
import { useAuthCapabilities, useContentPolicy, type ContentPolicy, type ContentPolicyInput } from '../api/contentPolicy'
import { useUi } from '../store/ui'
import { useSession } from '../store/session'
import { Button, Chip, ErrorState, Modal } from './ui'
import { FocusGroup } from '../nav/Focusable'

const MOVIES = ['G', 'PG', 'PG-13', 'R', 'NC-17']
const TV = ['TV-Y', 'TV-Y7', 'TV-G', 'TV-PG', 'TV-14', 'TV-MA']

export function ContentPolicySettings() {
  const capability = useAuthCapabilities()
  const me = useSession((s) => s.profile)!
  if (capability.isPending) return <p role="status">Checking content-control availability…</p>
  if (capability.error) return <ErrorState error={capability.error} onRetry={() => void capability.refetch()} />
  if (!capability.data?.content_policy_enabled) return <p>Server-enforced content controls are not enabled. Profile labels and hidden groups are display preferences, not access restrictions.</p>
  if (!me.is_admin || me.content_restricted) return <p>Content restrictions are managed by the owner. They cannot be changed in this profile’s preferences.</p>
  if (!capability.data.owner_pin_configured) return <p>Owner PIN setup is required before restricted profiles can be enabled. Open Settings → Profiles → Owner PIN setup or recovery and request local VM approval.</p>
  return <AdminPolicies />
}

function AdminPolicies() {
  const profiles = useProfiles()
  const [target, setTarget] = useState<string | null>(null)
  const selected = profiles.data?.find((p) => p.id === target)
  const policy = useContentPolicy(target, !!selected && !selected.is_admin)
  return <section className="flex flex-col gap-4" data-testid="content-policies">
    <h2 className="heading">Profile content restrictions</h2>
    <p>Choose the profile to manage. Only explicitly allowed US movie and TV ratings are permitted when restrictions are on. Unknown or unrated content is blocked.</p>
    {profiles.error ? <ErrorState error={profiles.error} onRetry={() => void profiles.refetch()} /> : <FocusGroup focusKey="policy:profiles" className="flex gap-2 flex-wrap">
      {profiles.data?.map((p) => <Chip key={p.id} focusKey={`policy:profile:${p.id}`} pressed={target === p.id} onEnter={() => setTarget(p.id)}>{p.name}</Chip>)}
    </FocusGroup>}
    {target && (selected?.is_admin ? <p>Owner profiles cannot be restricted. Choose a separate profile to apply content restrictions.</p> : policy.error ? <ErrorState error={policy.error} onRetry={() => void policy.refetch()} /> : policy.data ? <PolicyEditor key={`${target}:${policy.dataUpdatedAt}`} policy={policy.data} name={profiles.data?.find((p) => p.id === target)?.name ?? 'selected profile'} /> : <p role="status">Loading this profile’s policy…</p>)}
  </section>
}

function PolicyEditor({ policy, name }: { policy: ContentPolicy; name: string }) {
  const me = useSession((s) => s.profile)!
  const clear = useSession((s) => s.clear)
  const qc = useQueryClient()
  const toast = useUi((s) => s.toast)
  const initial: ContentPolicyInput = { restricted: policy.restricted, allowed_movie_codes: policy.allowed_movie_codes, allowed_tv_codes: policy.allowed_tv_codes, allow_unknown: false }
  const [draft, setDraft] = useState(initial)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [armed,setArmed]=useState<ContentPolicyInput|null>(null)
  const authorizationGeneration=useRef(0)
  const saving=useRef(false)
  const toggle = (field: 'allowed_movie_codes' | 'allowed_tv_codes', code: string) => setDraft((d) => ({ ...d, [field]: d[field].includes(code) ? d[field].filter((v) => v !== code) : [...d[field], code] }))
  const save = async (proof:OwnerAction,signal:AbortSignal) => {
    if(!armed || signal.aborted)return
    if(proof.profile_id!==policy.profile_id || proof.action!=='content_policy' || !proof.owner_action_token)throw new Error('Owner approval does not match this content-policy action.')
    if(useSession.getState().generation!==authorizationGeneration.current)throw new Error('Sign-in changed. Review the policy and authorize again.')
    saving.current=true;setBusy(true);setMessage('');setError('')
    const originGeneration=authorizationGeneration.current
    try {
      await unwrap<ContentPolicy>(api.PUT('/profiles/{id}/content-policy', {params:{path:{id:policy.profile_id},header:{'X-Owner-Action':proof.owner_action_token}},body:armed,signal:AbortSignal.any([signal,AbortSignal.timeout(30_000)])}))
      setArmed(null)
      if(useSession.getState().generation!==originGeneration || useSession.getState().profile?.id!==me.id)return
      await qc.cancelQueries()
      if(useSession.getState().generation!==originGeneration || useSession.getState().profile?.id!==me.id)return
      qc.clear()
      if(policy.profile_id===me.id)clear()
      else toast(`Restrictions saved for ${name}. Their existing sessions must sign in again.`, 'success')
    } catch(e) {setArmed(null);setError(e instanceof Error && e.name==='TimeoutError'?'Save outcome is unknown. Reload this policy before authorizing another save.':e instanceof Error?e.message:'Policy could not be saved. Authorize again before retrying.')}
    finally{saving.current=false;setBusy(false)}
  }
  return <FocusGroup focusKey="policy:editor" className="panel flex flex-col gap-4" data-testid="policy-editor">
    <h3 className="heading">Restrictions for {name}</h3>
    <Button focusKey="policy:restricted" disabled={busy} pressed={draft.restricted} onEnter={() => setDraft((d) => ({ ...d, restricted: !d.restricted, allow_unknown: !d.restricted ? false : d.allow_unknown }))}>{draft.restricted ? 'Restrictions on' : 'Restrictions off'}</Button>
    <p>Exiting a restricted profile and saving content restrictions require the owner PIN. PIN setup and recovery require separate local VM approval.</p>
    <p className="label">Allow these movie ratings (each rating is independent)</p>
    <div className="flex gap-2 flex-wrap">{MOVIES.map((code) => <Button key={code} focusKey={`policy:movie:${code}`} pressed={draft.allowed_movie_codes.includes(code)} disabled={busy || !draft.restricted} onEnter={() => toggle('allowed_movie_codes', code)}>{code}</Button>)}</div>
    <p className="label">Allow these TV ratings</p>
    <div className="flex gap-2 flex-wrap">{TV.map((code) => <Button key={code} focusKey={`policy:tv:${code}`} pressed={draft.allowed_tv_codes.includes(code)} disabled={busy || !draft.restricted} onEnter={() => toggle('allowed_tv_codes', code)}>{code}</Button>)}</div>
    <p>Unrated and unknown content is blocked for restricted profiles.</p>
    <p className="label">Saving invalidates this profile’s existing sessions.{policy.profile_id === me.id ? ' This is your profile: saving signs you out.' : ''}</p>
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    <div className="flex gap-2"><Button focusKey="policy:save" primary disabled={busy || !policy.feature_enabled} onEnter={() => {authorizationGeneration.current=useSession.getState().generation;setArmed({...draft,allowed_movie_codes:[...draft.allowed_movie_codes],allowed_tv_codes:[...draft.allowed_tv_codes]})}}>{busy ? 'Saving…' : `Save restrictions for ${name}`}</Button><Button focusKey="policy:reset" disabled={busy} onEnter={() => { setDraft(initial); setError(''); setMessage('') }}>Discard changes</Button></div>
    <Modal open={!!armed} onClose={()=>{if(!saving.current)setArmed(null)}} title={`Authorize restrictions for ${name}`} firstFocusKey="policy-auth:cancel" wide>
      {armed&&<OwnerPin targetId={policy.profile_id} targetName={name} action="admin" onApproved={async(result,signal)=>{if(!('owner_action_token' in result))throw new Error('Owner approval returned a different action.');await save(result,signal)}}/>}
      {busy&&<p role="status">Saving restrictions. Please wait for the result.</p>}
      <Button focusKey="policy-auth:cancel" disabled={busy} onEnter={()=>{if(!saving.current)setArmed(null)}}>Cancel</Button>
    </Modal>
  </FocusGroup>
}
