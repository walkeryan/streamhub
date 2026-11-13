import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router'
import { z } from 'zod'
import { ApiError, api, ok } from '../api/client'
import { useProfiles } from '../api/hooks'
import { useCan } from '../api/me'
import { people } from '../api/people'
import { Toggle } from '../components/PeopleControls'
import { Button, Chip } from '../components/ui'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { useRestoreFocus } from '../nav/back'
import { setFocus } from '../nav/spatial'
import { deviceName } from '../lib/people'
import { useSession } from '../store/session'

const codeSchema = z.string().trim().toUpperCase().regex(/^[A-Z0-9]{6}$/, 'The code has 6 letters and numbers.')
/** What the server knows about the device that asked for a code. `null`: a server that cannot say. */
type Asking = { ip?: string | null; user_agent?: string | null } | null

/**
 * Connect a TV: the code on its screen signs it in as the person approving here. That makes a link
 * with a code in it dangerous (someone else's device, one click from being signed in as you), so
 * approval is never one click from a link. The code is entered or checked first; then a second
 * screen says what is asking, from where, and as whom, and only there is it approved.
 */
export function Pair() {
  const [sp] = useSearchParams()
  const me = useSession((s) => s.profile)!
  const profiles = useProfiles()
  const fromLink = (sp.get('code') ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 6)
  const [code, setCode] = useState(fromLink)
  const [profileId, setProfileId] = useState(me.id)
  // Only someone who manages users may make a device a household one; the server checks the same.
  const manages = useCan('users.manage')
  const [shared, setShared] = useState(false)
  const [state, setState] = useState<'idle' | 'checking' | 'review' | 'busy' | 'done'>('idle')
  const [asking, setAsking] = useState<Asking>(null)
  const [err, setErr] = useState<string | null>(null)
  useRestoreFocus(true, 'pair:code')
  // The review opens with the cursor on the way out, never on Approve.
  useEffect(() => {
    if (state !== 'review') return
    const id = window.setTimeout(() => void setFocus('pair:cancel'), 50)
    return () => window.clearTimeout(id)
  }, [state])
  const as = profiles.data?.find((p) => p.id === profileId)?.name ?? me.name

  const review = async () => {
    const r = codeSchema.safeParse(code)
    if (!r.success) { setErr(r.error.issues[0]?.message ?? 'Check the code.'); return }
    setErr(null); setState('checking')
    try { setAsking(await people.lookupDevice(r.data)) } catch (e) {
      // A server from before this question could be asked: the review still happens, without the details.
      const older = e instanceof ApiError && (e.status === 405 || (e.status === 404 && /no such endpoint/i.test(e.message)))
      if (!older) { setState('idle'); setErr(e instanceof ApiError && e.status === 404 ? 'That code is not valid or has expired. Check the TV and try again.' : e instanceof Error ? e.message : 'Could not check that code.'); return }
      setAsking(null)
    }
    setState('review')
  }
  const approve = async () => {
    const r = codeSchema.safeParse(code)
    if (!r.success) return
    setErr(null); setState('busy')
    try {
      // shared_device is newer than the generated contract; it is sent only when asked for.
      const body: { user_code: string; profile_id: string; shared_device?: boolean } = { user_code: r.data, profile_id: profileId, ...(manages && shared ? { shared_device: true } : {}) }
      await ok(api.POST('/auth/device/approve', { body }))
      setState('done')
    } catch (e) { setState('review'); setErr(e instanceof Error ? e.message : 'Could not approve that code.') }
  }
  const back = () => { setState('idle'); setErr(null); window.setTimeout(() => void setFocus('pair:code'), 30) }
  return (
    <div className="page-pad flex flex-col gap-5" style={{ maxWidth: '38rem' }} data-testid="pair">
      <h1 className="display">Connect a TV</h1>
      {state === 'done' ? (
        <div className="panel flex flex-col gap-3" role="status" data-testid="pair-done">
          <h2 className="title-xl">TV connected</h2>
          <p>Your TV is signing in as {as}. You can close this page.</p>
          {manages && shared && <p className="muted" data-testid="pair-shared">It is a shared household device: it opens on "Who's watching?".</p>}
          <div><Button focusKey="pair:again" onEnter={() => { setCode(''); setState('idle') }}>Connect another TV</Button></div>
        </div>
      ) : state === 'review' || state === 'busy' ? (
        // Keyed: the two steps sit at the same place in the tree, and a focusable keeps the focus key it was mounted with.
        <FocusGroup key="review" focusKey="pair:review" className="panel flex flex-col gap-4" data-testid="pair-review-panel">
          <h2 className="title-xl">A TV is asking to sign in as {as}</h2>
          <p role="alert">Only approve if you are holding its remote and it shows this code. Approving signs that device in as {as}{manages && shared ? ', as a shared household device' : ''}: whoever has it can watch as {as === me.name ? 'you' : as}.</p>
          <p className="display" style={{ letterSpacing: '0.2em' }} data-testid="pair-code-shown" aria-label={`Code ${code.split('').join(' ')}`}>{code}</p>
          <p className="label" data-testid="pair-asking">{asking && (asking.ip || asking.user_agent)
            ? `Asked for from ${[asking.ip, asking.user_agent ? deviceName(null, 'tv', asking.user_agent) : null, asking.user_agent].filter(Boolean).join(' · ')}`
            : 'This server cannot say where the request came from. Check the code on the TV itself.'}</p>
          <p className="muted">Did someone send you this code or a link to this page? Then it is their device, not yours. Do not approve it.</p>
          {err && <p role="alert" style={{ color: 'var(--t-danger)' }}>{err}</p>}
          <div className="flex gap-3 flex-wrap">
            <Button focusKey="pair:cancel" disabled={state === 'busy'} onEnter={back} testId="pair-cancel">Not my TV</Button>
            <Button focusKey="pair:go" primary disabled={state === 'busy'} onEnter={() => void approve()} testId="pair-approve">{state === 'busy' ? 'Approving…' : 'Approve this TV'}</Button>
          </div>
        </FocusGroup>
      ) : (
        <FocusGroup key="form" focusKey="pair:form" className="flex flex-col gap-4">
          <p className="muted">Open StreamHub on your TV. It shows a 6-character code. Enter it here to sign the TV in.</p>
          {fromLink && code === fromLink && <p className="panel" role="note" data-testid="pair-from-link">This page was opened with a code already filled in. Go on only if you are setting up a TV yourself right now and it shows <strong>{fromLink}</strong>. Nothing is approved until you confirm it on the next screen.</p>}
          <FocusableInput focusKey="pair:code" label="TV code" placeholder="ABC123" maxLength={6} autoCapitalize="characters" value={code}
            onChange={(v) => setCode(v.toUpperCase().replace(/[^A-Z0-9]/g, ''))} onSubmit={() => void review()} className="pair-code" />
          {(profiles.data?.length ?? 0) > 1 && (
            <div className="flex flex-col gap-2"><span className="label">Sign the TV in as</span>
              <div className="flex gap-2 flex-wrap">{profiles.data!.filter((p) => !p.has_pin || p.id === me.id).map((p) => <Chip key={p.id} focusKey={`pair:p:${p.id}`} pressed={profileId === p.id} onEnter={() => setProfileId(p.id)}>{p.name}</Chip>)}</div>
            </div>
          )}
          {manages && <Toggle focusKey="pair:shared" label="This is a shared household device" hint={'A living-room TV, say. It opens on "Who\'s watching?" and anyone in the household can pick themselves.'}
            on={shared} onChange={setShared} testId="pair-shared-toggle" />}
          {err && <p role="alert" style={{ color: 'var(--t-danger)' }}>{err}</p>}
          <div><Button focusKey="pair:next" primary disabled={state === 'checking' || code.length !== 6} onEnter={() => void review()} testId="pair-review">{state === 'checking' ? 'Checking…' : 'Continue'}</Button></div>
        </FocusGroup>
      )}
    </div>
  )
}
