import { useEffect, useState } from 'react'
import { useAuthCapabilities } from '../api/contentPolicy'
import { refreshMe } from '../api/client'
import { useMe } from '../api/me'
import { METHOD_NAME, finishPlex, goToPlex, passwordProblem, signin, signInError, takePlexState, usernameProblem, type SignInMethod } from '../api/signin'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { useUi } from '../store/ui'
import { Button, Modal } from './ui'

const ORDER: SignInMethod[] = ['google', 'plex', 'password']
// What linking came back with, read once per page load however often React mounts this.
let returned: { plex: string | null; linked: string | null; failed: string | null } | undefined
function takeReturn() {
  if (returned) return returned
  const plex = takePlexState()
  const url = new URL(window.location.href)
  returned = { plex, linked: url.searchParams.get('linked'), failed: url.searchParams.get('link_error') }
  url.searchParams.delete('linked'); url.searchParams.delete('link_error')
  window.history.replaceState(window.history.state, '', url.pathname + url.search)
  return returned
}
const LINK_FAILED: Record<string, string> = {
  identity_in_use: 'That account already signs in as someone else here.',
  already_linked: 'Another account of that kind is linked already. Unlink it first.',
}

/**
 * Settings: the ways the signed-in person can sign in, and linking or removing one. Linking Google
 * or Plex runs that sign-in again while signed in; the server attaches the identity and refuses one
 * that is somebody else's, and it never lets the last way in be removed.
 */
export function SignInMethods() {
  const me = useMe()
  const config = useAuthCapabilities()
  const toast = useUi((s) => s.toast)
  const [busy, setBusy] = useState<SignInMethod | null>(null)
  const [editing, setEditing] = useState(false)
  const [removing, setRemoving] = useState<SignInMethod | null>(null)
  const user = me.data?.user
  const held = user?.sign_in_methods ?? []

  useEffect(() => {
    const back = takeReturn()
    if (back.linked === 'google') { toast('Google is linked. You can sign in with it from now on.', 'success'); refreshMe() }
    if (back.failed) toast(LINK_FAILED[back.failed] ?? 'Google could not be linked. Please try again.', 'error')
    if (back.plex) {
      const state = back.plex
      void finishPlex(state).then((outcome) => {
        if (outcome.status !== 'linked') throw new Error('That Plex sign-in was not started from here.')
        toast('Plex is linked. You can sign in with it from now on.', 'success'); refreshMe()
      }).catch((e: unknown) => toast(signInError(e, 'Plex could not be linked. Please try again.'), 'error'))
    }
    returned = { plex: null, linked: null, failed: null }
  }, [toast])

  // A server from before users has no sign-in methods to show.
  if (!user || !me.data || me.data.shared_device) return null
  const c = config.data
  const offered = (m: SignInMethod) => held.includes(m) || (m === 'google' ? !!c?.google_enabled : m === 'plex' ? !!c?.plex_enabled : !!c?.password_enabled)
  const label = (m: SignInMethod) => (m === 'google' ? user.email : m === 'plex' ? user.plex_username : user.username) ?? null
  const link = (m: SignInMethod) => {
    if (m === 'password') { setEditing(true); return }
    setBusy(m)
    const going = m === 'google' ? signin.linkGoogle() : goToPlex('/settings', { link: true })
    going.catch((e: unknown) => { setBusy(null); toast(signInError(e, `${METHOD_NAME[m]} could not be linked.`), 'error') })
  }
  const remove = async (m: SignInMethod) => {
    setBusy(m)
    try { await signin.unlink(m); toast(`${METHOD_NAME[m]} sign-in removed.`, 'success'); refreshMe() }
    catch (e) { toast(signInError(e, 'That could not be removed.'), 'error') }
    finally { setBusy(null); setRemoving(null); void setFocus(`signin:${m}:link`) }
  }
  const shown = ORDER.filter(offered)
  if (!shown.length) return null
  return (
    <section className="flex flex-col gap-3" data-testid="signin-methods">
      <h2 className="heading">Sign-in methods</h2>
      <p className="muted">How you prove who you are from anywhere. Link more than one and any of them signs you in. The last one cannot be removed.</p>
      <FocusGroup focusKey="signin:list" className="flex flex-col gap-2">
        {shown.map((m) => {
          const linked = held.includes(m)
          const only = linked && held.length === 1
          return (
            <div key={m} className="panel person-row" data-testid={`signin-method-${m}`} data-linked={linked}>
              <span className="who">
                <span className="flex items-center gap-2 flex-wrap"><strong>{METHOD_NAME[m]}</strong><span className={`badge ${linked ? 'badge-ok' : ''}`}>{linked ? 'Linked' : 'Not linked'}</span></span>
                <span className="label truncate">{linked ? [label(m), only ? 'Your only way to sign in' : null].filter(Boolean).join(' · ') || 'Linked' : m === 'password' ? 'Choose a username and a password' : `Sign in with your ${METHOD_NAME[m]} account`}</span>
              </span>
              {linked && m === 'password' && <Button focusKey="signin:password:change" disabled={busy !== null} onEnter={() => setEditing(true)} testId="signin-change-password">Change password</Button>}
              {linked
                ? <Button key="unlink" focusKey={`signin:${m}:unlink`} danger disabled={busy !== null || only} label={`Remove ${METHOD_NAME[m]} sign-in`} onEnter={() => setRemoving(m)} testId={`signin-unlink-${m}`}>Remove</Button>
                : <Button key="link" focusKey={`signin:${m}:link`} disabled={busy !== null} label={`Link ${METHOD_NAME[m]}`} onEnter={() => link(m)} testId={`signin-link-${m}`}>{busy === m ? 'Opening…' : m === 'password' ? 'Set a password' : 'Link'}</Button>}
            </div>
          )
        })}
      </FocusGroup>
      {editing && <PasswordDialog has={held.includes('password')} username={user.username ?? null} onClose={() => setEditing(false)}
        onSaved={(changed) => { setEditing(false); toast(changed ? 'Password changed. Your other devices were signed out.' : 'Password set. You can sign in with it from now on.', 'success'); refreshMe() }} />}
      <Modal open={!!removing} onClose={() => setRemoving(null)} title={removing ? `Remove ${METHOD_NAME[removing]} sign-in?` : ''} firstFocusKey="signin:keep">
        <p>You will no longer be able to sign in that way. Your other sign-in methods keep working, and this device stays signed in.</p>
        <div className="flex gap-3 flex-wrap">
          <Button focusKey="signin:keep" onEnter={() => setRemoving(null)}>Keep it</Button>
          <Button focusKey="signin:remove-ok" danger disabled={busy !== null} onEnter={() => { if (removing) void remove(removing) }} testId="signin-unlink-ok">Remove</Button>
        </div>
      </Modal>
    </section>
  )
}

/** Set a first password (with a username), or change the current one, which needs the current one. */
function PasswordDialog({ has, username, onClose, onSaved }: { has: boolean; username: string | null; onClose: () => void; onSaved: (changed: boolean) => void }) {
  const [name, setName] = useState(username ?? '')
  const [current, setCurrent] = useState('')
  const [password, setPassword] = useState('')
  const [again, setAgain] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const clean = (username ?? name).trim().toLowerCase()
  const submit = async () => {
    if (busy) return
    const problem = (username ? null : usernameProblem(clean)) ?? (has && !current ? 'Enter your current password.' : null) ?? passwordProblem(clean, password, again)
    if (problem) { setError(problem); return }
    setBusy(true); setError(null)
    try {
      await signin.setPassword({ new_password: password, ...(has ? { current_password: current } : {}), ...(username ? {} : { username: clean }) })
      onSaved(has)
    } catch (e) { setError(signInError(e, 'The password could not be saved.')) } finally { setBusy(false) }
  }
  return (
    <Modal open onClose={onClose} title={has ? 'Change password' : 'Set a username and password'} firstFocusKey={has ? 'pw:current' : username ? 'pw:new' : 'pw:username'} testId="password-dialog">
      {username ? <p>Your username is <strong>{username}</strong>.</p>
        : <FocusableInput focusKey="pw:username" label="Username" placeholder="Username" maxLength={32} autoCapitalize="none" autoComplete="username" value={name} onChange={setName} onSubmit={() => void setFocus('pw:new')} />}
      {has && <FocusableInput focusKey="pw:current" label="Current password" placeholder="Current password" type="password" maxLength={256} autoComplete="current-password" value={current} onChange={setCurrent} onSubmit={() => void setFocus('pw:new')} />}
      <FocusableInput focusKey="pw:new" label="New password" placeholder="At least 10 characters" type="password" maxLength={256} autoComplete="new-password" value={password} onChange={setPassword} onSubmit={() => void setFocus('pw:again')} />
      <FocusableInput focusKey="pw:again" label="New password again" placeholder="The same password again" type="password" maxLength={256} autoComplete="new-password" value={again} onChange={setAgain} onSubmit={() => void submit()} />
      <p className="label">{has ? 'Changing it signs your other devices out.' : 'At least 10 characters, not containing the username.'}</p>
      {error && <p role="alert" style={{ color: 'var(--t-danger)' }} data-testid="password-dialog-error">{error}</p>}
      <div className="flex gap-3 flex-wrap">
        <Button focusKey="pw:save" primary disabled={busy} onEnter={() => void submit()} testId="password-save">{busy ? 'Saving…' : 'Save password'}</Button>
        <Button focusKey="pw:cancel" onEnter={onClose}>Cancel</Button>
      </div>
    </Modal>
  )
}
