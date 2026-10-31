import { useState } from 'react'
import { useSearchParams } from 'react-router'
import { z } from 'zod'
import { api, ok } from '../api/client'
import { useProfiles } from '../api/hooks'
import { Button, Chip } from '../components/ui'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { useRestoreFocus } from '../nav/back'
import { useSession } from '../store/session'

const codeSchema = z.string().trim().toUpperCase().regex(/^[A-Z0-9]{6}$/, 'The code has 6 letters and numbers.')

export function Pair() {
  const [sp] = useSearchParams()
  const me = useSession((s) => s.profile)!
  const profiles = useProfiles()
  const [code, setCode] = useState((sp.get('code') ?? '').toUpperCase().slice(0, 6))
  const [profileId, setProfileId] = useState(me.id)
  const [state, setState] = useState<'idle' | 'busy' | 'done'>('idle')
  const [err, setErr] = useState<string | null>(null)
  useRestoreFocus(true, 'pair:code')

  const approve = async () => {
    const r = codeSchema.safeParse(code)
    if (!r.success) { setErr(r.error.issues[0]?.message ?? 'Check the code.'); return }
    setErr(null); setState('busy')
    try {
      await ok(api.POST('/auth/device/approve', { body: { user_code: r.data, profile_id: profileId } }))
      setState('done')
    } catch (e) { setState('idle'); setErr(e instanceof Error ? e.message : 'Could not approve that code.') }
  }
  return (
    <div className="page-pad flex flex-col gap-5" style={{ maxWidth: '38rem' }} data-testid="pair">
      <h1 className="display">Connect a TV</h1>
      <p className="muted">Open StreamHub on your TV. It shows a 6-character code. Enter it here to sign the TV in.</p>
      {state === 'done' ? (
        <div className="panel flex flex-col gap-3" role="status" data-testid="pair-done">
          <h2 className="title-xl">TV connected</h2>
          <p>Your TV is signing in as {profiles.data?.find((p) => p.id === profileId)?.name ?? me.name}. You can close this page.</p>
          <div><Button focusKey="pair:again" onEnter={() => { setCode(''); setState('idle') }}>Connect another TV</Button></div>
        </div>
      ) : (
        <FocusGroup focusKey="pair:form" className="flex flex-col gap-4">
          <FocusableInput focusKey="pair:code" label="TV code" placeholder="ABC123" maxLength={6} autoCapitalize="characters" value={code}
            onChange={(v) => setCode(v.toUpperCase().replace(/[^A-Z0-9]/g, ''))} onSubmit={() => void approve()} className="pair-code" />
          {(profiles.data?.length ?? 0) > 1 && (
            <div className="flex flex-col gap-2"><span className="label">Sign the TV in as</span>
              <div className="flex gap-2 flex-wrap">{profiles.data!.filter((p) => !p.has_pin || p.id === me.id).map((p) => <Chip key={p.id} focusKey={`pair:p:${p.id}`} pressed={profileId === p.id} onEnter={() => setProfileId(p.id)}>{p.name}</Chip>)}</div>
            </div>
          )}
          {err && <p role="alert" style={{ color: 'var(--t-danger)' }}>{err}</p>}
          <div><Button focusKey="pair:go" primary disabled={state === 'busy' || code.length !== 6} onEnter={() => void approve()} testId="pair-approve">{state === 'busy' ? 'Approving…' : 'Approve TV'}</Button></div>
        </FocusGroup>
      )}
    </div>
  )
}
