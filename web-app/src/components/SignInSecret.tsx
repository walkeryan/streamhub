import { useEffect, useState } from 'react'
import { ApiError } from '../api/client'
import type { PinKind } from '../api/people'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { useUi } from '../store/ui'
import { ArrowPinAskDialog, ArrowPinDialog } from './ArrowPin'
import { PinDialog, explain } from './PinPad'
import { Button, Modal } from './ui'
import { GlassIconButton } from '../ds/GlassIconButton'

/**
 * The one secret a person signs in with on shared devices, and opens the adult category with: a
 * number PIN, a D-pad arrow pattern, or a password. Each person picks exactly one. The hub keeps them
 * all the same way; the kind only says how the secret is asked for.
 */
export type { PinKind }
type Holder = { has_pin?: boolean; pin_kind?: PinKind | null; pin_arrows?: number | null }

/** Which kind someone's secret is, or null when they have none. A hub from before kinds says only has_pin and pin_arrows. */
export function kindOf(who: Holder | null | undefined): PinKind | null {
  if (!who?.has_pin) return null
  return who.pin_kind ?? (who.pin_arrows ? 'arrows' : 'pin')
}

/** The secret's name in a sentence: "That PIN is not right", "Enter their password". */
export const NOUN: Record<PinKind, string> = { pin: 'PIN', arrows: 'arrow pattern', password: 'password' }
export const PASSWORD_MIN = 6, PASSWORD_MAX = 64

/** How someone signs in, as one line: "Signs in with a password". */
export function signsInWith(who: Holder | null | undefined): string {
  const kind = kindOf(who)
  return kind === 'password' ? 'Signs in with a password'
    : kind === 'arrows' ? `Signs in with an arrow pattern${who?.pin_arrows ? ` of ${who.pin_arrows} arrows` : ''}`
    : kind === 'pin' ? 'Signs in with a number PIN'
    : 'No PIN'
}

const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`

/**
 * A password, typed: asked for (`confirm` unset), or chosen and typed twice (`confirm`). Focus starts
 * in the field, which on a TV brings up the system keyboard; Enter submits (or, when choosing, moves
 * to the second field). Taken exactly as typed, spaces included, as the hub stores it. A 429 locks it
 * for the server's Retry-After.
 */
export function PasswordDialog(p: {
  open: boolean; title: string; hint?: string; confirm?: boolean; submitLabel?: string
  onSubmit: (password: string) => Promise<void>; onClose: () => void; explain?: (code: string | undefined) => string | undefined
}) {
  const [value, setValue] = useState('')
  const [again, setAgain] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [lockedUntil, setLockedUntil] = useState(0)
  const [now, setNow] = useState(() => Date.now())
  const left = Math.max(0, Math.ceil((lockedUntil - now) / 1000))
  const locked = left > 0
  // A fresh dialog each time it opens.
  const [wasOpen, setWasOpen] = useState(p.open)
  if (wasOpen !== p.open) { setWasOpen(p.open); if (p.open) { setValue(''); setAgain(''); setError(null); setBusy(false) } }
  useEffect(() => {
    if (!locked) return
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [locked])

  const submit = async () => {
    if (busy || locked || !value) return
    if (p.confirm) {
      if (value.length < PASSWORD_MIN || value.length > PASSWORD_MAX) { setError(`Use ${PASSWORD_MIN} to ${PASSWORD_MAX} characters.`); return }
      if (value !== again) { setError('Those two passwords are not the same.'); void setFocus('pw:again'); return }
    }
    setBusy(true); setError(null)
    try { await p.onSubmit(value) } catch (e) {
      setValue(''); setAgain('')
      setError(explain(e, p.explain, 'password'))
      if (e instanceof ApiError && e.status === 429) { setNow(Date.now()); setLockedUntil(Date.now() + (e.retryAfter ?? 60) * 1000) }
      void setFocus('pw:input')
    } finally { setBusy(false) }
  }
  return (
    <Modal open={p.open} onClose={p.onClose} title={p.title} firstFocusKey="pw:input" testId="password-dialog">
      <div className="secret-form">
        {p.hint && <p className="acct-note">{p.hint}</p>}
        <div className="acct-field">
          <span className="acct-kicker" aria-hidden="true">Password</span>
          <FocusableInput focusKey="pw:input" label="Password" type="password" autoComplete={p.confirm ? 'new-password' : 'current-password'} maxLength={PASSWORD_MAX}
            placeholder={p.confirm ? `${PASSWORD_MIN} to ${PASSWORD_MAX} characters` : 'Password'} readOnly={busy || locked} value={value} onChange={setValue}
            onSubmit={() => (p.confirm && !again ? void setFocus('pw:again') : void submit())} />
        </div>
        {p.confirm && <div className="acct-field">
          <span className="acct-kicker" aria-hidden="true">Password again</span>
          <FocusableInput focusKey="pw:again" label="Password again" type="password" autoComplete="new-password" maxLength={PASSWORD_MAX}
            placeholder="Once more" readOnly={busy} value={again} onChange={setAgain} onSubmit={() => void submit()} />
        </div>}
        {locked
          ? <p role="alert" data-testid="password-locked" className="acct-error">Too many wrong passwords. Try again in {clock(left)}.</p>
          : error && <p role="alert" data-testid="password-error" className="acct-error">{error}</p>}
        <div className="acct-actions">
          <Button focusKey="pw:ok" primary disabled={busy || locked || !value} onEnter={() => void submit()} testId="password-ok">{p.submitLabel ?? 'OK'}</Button>
          <Button focusKey="pw:cancel" onEnter={p.onClose}>Cancel</Button>
        </div>
      </div>
    </Modal>
  )
}

/**
 * Asking for someone's own secret the way they chose it: the keypad for a number PIN, the dots that
 * fill with arrow presses for an arrow pattern, a password field for a password. `title` and `hint`
 * are per kind, so each can name what is asked for.
 */
export function AskSecretDialog(p: {
  open: boolean; kind: PinKind; arrows?: number | null; title: Record<PinKind, string>; hint: Record<PinKind, string>; submitLabel?: string
  onSubmit: (secret: string) => Promise<void>; onClose: () => void; explain?: (code: string | undefined) => string | undefined
}) {
  const common = { open: p.open, onSubmit: p.onSubmit, onClose: p.onClose, explain: p.explain }
  // An arrow pattern whose length the hub did not say is still its digits: the keypad takes it.
  if (p.kind === 'arrows' && p.arrows) return <ArrowPinAskDialog {...common} title={p.title.arrows} hint={p.hint.arrows} length={p.arrows} />
  if (p.kind === 'password') return <PasswordDialog {...common} title={p.title.password} hint={p.hint.password} submitLabel={p.submitLabel} />
  return <PinDialog {...common} title={p.title[p.kind]} hint={p.hint[p.kind]} submitLabel={p.submitLabel} />
}

const KIND_ICON: Record<PinKind, string> = { pin: 'dots', password: 'lock', arrows: 'up' }
const CHOICES: { kind: PinKind; name: string; detail: string }[] = [
  { kind: 'pin', name: 'Number PIN', detail: '4 to 8 digits, on a keypad.' },
  { kind: 'password', name: 'Password', detail: `${PASSWORD_MIN} to ${PASSWORD_MAX} characters, typed. A TV shows its keyboard.` },
  { kind: 'arrows', name: 'Arrow pattern', detail: '4 to 8 presses of the arrows on a remote or keyboard, with no OK.' },
]

/**
 * Choosing how someone signs in: one of a number PIN, a password or an arrow pattern, then that
 * secret, entered twice where it is typed. `onSave` resolves when the hub took it; when it throws, the
 * entry stays open and says why.
 */
export function ChooseSecretDialog(p: {
  open: boolean; name: string; scope: string; current: PinKind | null; note?: string
  onSave: (secret: string, kind: PinKind) => Promise<void>; onClose: () => void; explain?: (code: string | undefined) => string | undefined
}) {
  const [kind, setKind] = useState<PinKind | null>(null)
  const [wasOpen, setWasOpen] = useState(p.open)
  if (wasOpen !== p.open) { setWasOpen(p.open); if (p.open) setKind(null) }
  const save = (k: PinKind) => (secret: string) => p.onSave(secret, k)
  return <>
    <Modal open={p.open && !kind} onClose={p.onClose} title={`How should ${p.name} sign in?`} firstFocusKey={`${p.scope}:kind:${p.current ?? 'pin'}`} testId="secret-choice">
      <p className="acct-note">Pick one. It is asked for when a shared device switches to {p.name}, and to show adult titles.</p>
      {p.note && <p className="acct-note">{p.note}</p>}
      <FocusGroup focusKey={`${p.scope}:kinds`} className="acct-list" role="group" label="Sign in with">
        {CHOICES.map((c) => (
          <Button key={c.kind} focusKey={`${p.scope}:kind:${c.kind}`} className="secret-choice" icon={KIND_ICON[c.kind]} pressed={p.current === c.kind} onEnter={() => setKind(c.kind)} testId={`secret-kind-${c.kind}`}>
            <span className="flex flex-col"><strong>{c.name}{p.current === c.kind ? ' (now)' : ''}</strong><span className="acct-small">{c.detail}</span></span>
          </Button>
        ))}
      </FocusGroup>
      <div><Button focusKey={`${p.scope}:kind-cancel`} ghost onEnter={p.onClose}>Cancel</Button></div>
    </Modal>
    <PinDialog open={p.open && kind === 'pin'} onClose={p.onClose} title={`New PIN for ${p.name}`} hint="Four to eight digits." confirm submitLabel="Save PIN"
      onSubmit={save('pin')} explain={p.explain} />
    <PasswordDialog open={p.open && kind === 'password'} onClose={p.onClose} title={`New password for ${p.name}`} confirm submitLabel="Save password"
      hint={`${PASSWORD_MIN} to ${PASSWORD_MAX} characters. It is only for "Who's watching?" and adult titles, separate from any account password.`}
      onSubmit={save('password')} explain={p.explain} />
    <ArrowPinDialog open={p.open && kind === 'arrows'} onClose={p.onClose} title={`Arrow pattern for ${p.name}`} onSubmit={save('arrows')} explain={p.explain} />
  </>
}

/**
 * How one person signs in, and the ways to change or remove it. `save` sends the new secret (null
 * removes it), with `current` when the hub asked for the current one first (403 current_pin_required:
 * one's own secret, unless signed in a few minutes ago). That proof is then asked for the way the
 * current secret was chosen, and the change is sent again with it.
 */
export function SecretControls(p: {
  scope: string; name: string; who: Holder; testId: string; noneHint: string; setHint: string
  save: (pin: string | null, kind: PinKind | null, current?: string) => Promise<void>
}) {
  const toast = useUi((s) => s.toast)
  const kind = kindOf(p.who)
  const [choosing, setChoosing] = useState(false)
  // A change waiting for the current secret before the hub takes it.
  const [proving, setProving] = useState<{ pin: string | null; kind: PinKind | null } | null>(null)
  const done = (pin: string | null, k: PinKind | null) => {
    setChoosing(false); setProving(null)
    toast(!pin ? `${p.name} no longer has a PIN.` : `${p.name} now signs in with ${k === 'password' ? 'a password' : k === 'arrows' ? 'an arrow pattern' : 'a number PIN'}.`, 'success')
    void setFocus(`${p.scope}:pin`)
  }
  const attempt = async (pin: string | null, k: PinKind | null) => {
    try { await p.save(pin, k) } catch (e) {
      if (!(e instanceof ApiError && e.code === 'current_pin_required') || !kind) throw e
      setChoosing(false); setProving({ pin, kind: k })
      return
    }
    done(pin, k)
  }
  const remove = () => { attempt(null, null).catch((e: unknown) => toast(e instanceof Error ? e.message : 'Could not remove the PIN.', 'error')) }
  const noun = kind ? NOUN[kind] : 'PIN'
  return (
    <div className="acct-row person-row device-row" data-testid={p.testId} data-kind={kind ?? ''}>
      <span className="who"><strong data-testid={`${p.testId}-kind`}>{signsInWith(p.who)}</strong>
        <span className="acct-row-meta">{kind ? p.setHint : p.noneHint}</span></span>
      <span className="row-actions">
        <Button focusKey={`${p.scope}:pin`} icon="lock" onEnter={() => setChoosing(true)} testId={`${p.testId}-set`}>{kind ? 'Change sign-in' : 'Choose sign-in'}</Button>
        {kind && <GlassIconButton focusKey={`${p.scope}:pin-clear`} icon="trash" label={`Remove ${noun}`} className="acct-danger" onEnter={remove} testId={`${p.testId}-clear`} />}
      </span>
      <ChooseSecretDialog open={choosing} name={p.name} scope={p.scope} current={kind} onClose={() => { setChoosing(false); void setFocus(`${p.scope}:pin`) }}
        onSave={(secret, k) => attempt(secret, k)} />
      {kind && <AskSecretDialog open={!!proving} kind={kind} arrows={p.who.pin_arrows} submitLabel="Continue"
        title={{ pin: 'Your current PIN', arrows: 'Your current arrow pattern', password: 'Your current password' }}
        hint={{ pin: 'Enter it to change or remove it.', arrows: 'Press it to change or remove it.', password: 'Enter it to change or remove it.' }}
        explain={(code) => (code === 'pin_invalid' ? `That is not your current ${noun}.` : undefined)}
        onClose={() => { setProving(null); void setFocus(`${p.scope}:pin`) }}
        onSubmit={async (current) => { const next = proving!; await p.save(next.pin, next.kind, current); done(next.pin, next.kind) }} />}
    </div>
  )
}
