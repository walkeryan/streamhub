import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '../api/client'
import { useProfiles } from '../api/hooks'
import { useAuthCapabilities, useContentPolicy, type ContentPolicy, type ContentPolicyInput } from '../api/contentPolicy'
import { useUi } from '../store/ui'
import { useSession } from '../store/session'
import { Button, Chip, ErrorState } from './ui'
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
  const toggle = (field: 'allowed_movie_codes' | 'allowed_tv_codes', code: string) => setDraft((d) => ({ ...d, [field]: d[field].includes(code) ? d[field].filter((v) => v !== code) : [...d[field], code] }))
  const save = async () => {
    setBusy(true); setMessage(''); setError('')
    const originGeneration = useSession.getState().generation
    try {
      await unwrap<ContentPolicy>(api.PUT('/profiles/{id}/content-policy', { params: { path: { id: policy.profile_id } }, body: draft }))
      if (useSession.getState().generation !== originGeneration || useSession.getState().profile?.id !== me.id) return
      await qc.cancelQueries()
      if (useSession.getState().generation !== originGeneration || useSession.getState().profile?.id !== me.id) return
      qc.clear()
      if (policy.profile_id === me.id) clear()
      else toast(`Restrictions saved for ${name}. Their existing sessions must sign in again.`, 'success')
    } catch (e) { setError(e instanceof Error ? e.message : 'Policy could not be saved.') }
    finally { setBusy(false) }
  }
  return <FocusGroup focusKey="policy:editor" className="panel flex flex-col gap-4" data-testid="policy-editor">
    <h3 className="heading">Restrictions for {name}</h3>
    <Button focusKey="policy:restricted" disabled={busy} pressed={draft.restricted} onEnter={() => setDraft((d) => ({ ...d, restricted: !d.restricted, allow_unknown: !d.restricted ? false : d.allow_unknown }))}>{draft.restricted ? 'Restrictions on' : 'Restrictions off'}</Button>
    <p>Exiting a restricted profile requires the owner to sign in again. No new PIN is created here.</p>
    <p className="label">Allow these movie ratings (each rating is independent)</p>
    <div className="flex gap-2 flex-wrap">{MOVIES.map((code) => <Button key={code} focusKey={`policy:movie:${code}`} pressed={draft.allowed_movie_codes.includes(code)} disabled={busy || !draft.restricted} onEnter={() => toggle('allowed_movie_codes', code)}>{code}</Button>)}</div>
    <p className="label">Allow these TV ratings</p>
    <div className="flex gap-2 flex-wrap">{TV.map((code) => <Button key={code} focusKey={`policy:tv:${code}`} pressed={draft.allowed_tv_codes.includes(code)} disabled={busy || !draft.restricted} onEnter={() => toggle('allowed_tv_codes', code)}>{code}</Button>)}</div>
    <p>Unrated and unknown content is blocked for restricted profiles.</p>
    <p className="label">Saving invalidates this profile’s existing sessions.{policy.profile_id === me.id ? ' This is your profile: saving signs you out.' : ''}</p>
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    <div className="flex gap-2"><Button focusKey="policy:save" primary disabled={busy || !policy.feature_enabled} onEnter={() => void save()}>{busy ? 'Saving…' : `Save restrictions for ${name}`}</Button><Button focusKey="policy:reset" disabled={busy} onEnter={() => { setDraft(initial); setError(''); setMessage('') }}>Discard changes</Button></div>
  </FocusGroup>
}
