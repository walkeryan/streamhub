import { useState } from 'react'
import { Navigate, useNavigate, useParams } from 'react-router'
import { absent } from '../api/client'
import { useTitle } from '../api/hooks'
import { useCan, useMe } from '../api/me'
import { people, useAbsent, usePeopleChange, useRoles, useUser, type AuthSession, type UserDetail as Detail } from '../api/people'
import type { components } from '../api/schema'
import { Avatar } from '../components/Avatar'
import { Icon } from '../components/Icon'
import { LimitsEditor, PermissionSwitches, Toggle } from '../components/PeopleControls'
import { PinDialog } from '../components/PinPad'
import { Button, Chip, ErrorState, Modal, Skeleton } from '../components/ui'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { useGo, useRestoreFocus } from '../nav/back'
import { setFocus } from '../nav/spatial'
import { DEVICE_KIND, SIGN_IN, STATUS, ago, deviceName, type Limits } from '../lib/people'
import { useUi } from '../store/ui'

type Permission = components['schemas']['Permission']
type Change = Parameters<typeof people.patch>[1]
const HISTORY_SHOWN = 8

/**
 * One person: role, permissions, limits, PIN, devices, history, and the ways to stop them. Every
 * control saves at once and reaches that user on their next request. The server decides what is
 * allowed (the last owner stays an owner, nobody edits their own access); its refusal is shown as it
 * is worded rather than second-guessed here.
 */
export function UserDetail() {
  const id = useParams().id
  const allowed = useCan('users')
  const user = useUser(allowed ? id : undefined)
  const roles = useRoles(allowed)
  const me = useMe().data
  const go = useGo()
  const navigate = useNavigate()
  const toast = useUi((s) => s.toast)
  const change = usePeopleChange((body: Change) => people.patch(id!, body))
  const pin = usePeopleChange((value: string | null) => people.setPin(id!, value))
  const status = usePeopleChange((to: 'suspend' | 'reinstate') => people[to](id!))
  const remove = usePeopleChange(() => people.remove(id!))
  const [asking, setAsking] = useState<'suspend' | 'remove' | 'pin' | null>(null)
  useRestoreFocus(!!user.data && !!roles.data, 'ud:back')
  if (!allowed) return <Navigate to="/" replace />
  // Back to the list this was opened from, where the remote's place is remembered; a deep link has no such list.
  const back = <div><Button focusKey="ud:back" icon="back" onEnter={() => ((window.history.state as { idx?: number } | null)?.idx ? navigate(-1) : navigate('/users', { replace: true }))} testId="user-back">Users</Button></div>
  if (user.error || roles.error) return <div className="page-pad flex flex-col gap-4" data-testid="user-detail">{back}<ErrorState error={user.error ?? roles.error} onRetry={() => { void user.refetch(); void roles.refetch() }} /></div>
  if (!user.data || !roles.data) return <div className="page-pad flex flex-col gap-4" data-testid="user-detail">{back}<Skeleton style={{ height: '18rem' }} /></div>

  const u = user.data
  const role = roles.data.find((r) => r.id === u.role_id)
  const inherited = role?.permissions ?? []
  const failed = (e: Error) => toast(e.message, 'error')
  const apply = (body: Change, done?: string) => change.mutate(body, { onSuccess: () => { if (done) toast(done, 'success') }, onError: failed })
  // An override is whatever differs from the role: a grant adds a permission, a denial takes one away.
  const without = (p: Permission) => ({ grants: u.grants.filter((g) => g !== p), denials: u.denials.filter((d) => d !== p) })
  const setPermission = (p: Permission, on: boolean) => {
    const next = without(p)
    if (on && !inherited.includes(p)) next.grants.push(p)
    if (!on && inherited.includes(p)) next.denials.push(p)
    apply(next)
  }
  // The overrides are sent whole: the fields present are the ones that stay overridden.
  const overrides = u.limit_overrides as Partial<Limits>
  const resetLimit = (key: keyof Limits) => apply({ limits: Object.fromEntries(Object.entries(overrides).filter(([k]) => k !== key)) })
  const sharedOverridden = u.on_shared_devices_override !== null && u.on_shared_devices_override !== undefined

  return (
    <div className="page-pad flex flex-col gap-6" style={{ maxWidth: '64rem' }} data-testid="user-detail" data-user={u.id}>
      {back}
      <header className="flex items-center gap-4 flex-wrap">
        <Avatar name={u.display_name} avatar={u.avatar} picture={u.picture} size={4.5} />
        <div className="flex flex-col gap-1 min-w-0" style={{ flex: '1 1 14rem' }}>
          <h1 className="title-xl flex items-center gap-2 flex-wrap">{u.display_name}{u.id === me?.user.id && <span className="badge">You</span>}</h1>
          <p className="label">{[u.email, `${SIGN_IN[u.kind] ?? u.kind} sign-in`, `Last seen ${ago(u.last_seen_at)}`].filter(Boolean).join(' · ')}</p>
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`badge ${u.status === 'active' ? 'badge-ok' : 'badge-warn'}`} data-testid="user-status">{STATUS[u.status] ?? u.status}</span>
            {u.now_playing.length > 0 && <span className="label flex items-center gap-1" style={{ color: 'var(--t-text)' }}><Icon name="play" size={12} />Watching {u.now_playing.map((n) => n.title ?? 'something').join(', ')}</span>}
          </div>
        </div>
      </header>

      <section className="panel flex flex-col gap-3" data-testid="user-role">
        <h2 className="heading">Role</h2>
        <FocusGroup focusKey="ud:roles" className="flex gap-2 flex-wrap" role="group" label="Role">
          {roles.data.map((r) => <Chip key={r.id} focusKey={`ud:role:${r.id}`} pressed={u.role_id === r.id} onEnter={() => { if (u.role_id !== r.id) apply({ role_id: r.id }, `${u.display_name} is now ${r.name}.`) }}>{r.name}</Chip>)}
        </FocusGroup>
        <p className="label">The role sets the starting permissions and limits. Anything changed below applies to {u.display_name} only.</p>
      </section>

      <section className="panel flex flex-col gap-3" data-testid="user-permissions">
        <h2 className="heading">Permissions</h2>
        <PermissionSwitches scope="ud:perm" held={u.permissions} inherited={inherited} onChange={setPermission} onReset={(p) => apply(without(p))} />
      </section>

      <section className="panel flex flex-col gap-3" data-testid="user-limits">
        <h2 className="heading">Limits</h2>
        <LimitsEditor scope="ud:limit" limits={u.limits} onChange={(c) => apply({ limits: { ...overrides, ...c } })}
          overridden={{ max_streams: 'max_streams' in overrides, rating_ceiling_movie: 'rating_ceiling_movie' in overrides, rating_ceiling_tv: 'rating_ceiling_tv' in overrides, allow_unrated: 'allow_unrated' in overrides }} onReset={resetLimit} />
      </section>

      <section className="panel flex flex-col gap-3" data-testid="user-sharing">
        <h2 className="heading">Shared devices and PIN</h2>
        <FocusGroup focusKey="ud:sharing" className="flex flex-col gap-2">
          <div className="toggle-row">
            <Toggle focusKey="ud:shared" label="Show on shared devices" hint={'Offered on "Who\'s watching?" on household TVs and browsers'} on={!!u.on_shared_devices}
              note={sharedOverridden ? 'Overridden' : 'From role'} onChange={(on) => apply({ on_shared_devices: on })} />
            {sharedOverridden && <Button focusKey="ud:shared:reset" ghost label="Reset shared devices to the role" onEnter={() => apply({ on_shared_devices: null })}>Reset</Button>}
          </div>
          <div className="person-row device-row">
            <span className="who"><strong>{u.has_pin ? 'Has a PIN' : 'No PIN'}</strong>
              <span className="label">{u.has_pin ? 'Asked for when a shared device switches to them.' : 'Anyone on a shared device with less access cannot switch to them until a PIN is set.'}</span></span>
            <Button focusKey="ud:pin" icon="lock" onEnter={() => setAsking('pin')} testId="user-pin-set">{u.has_pin ? 'Change PIN' : 'Set PIN'}</Button>
            {u.has_pin && <Button focusKey="ud:pin-clear" danger testId="user-pin-clear"
              onEnter={() => { void setFocus('ud:pin'); pin.mutate(null, { onSuccess: () => toast('PIN cleared.', 'success'), onError: failed }) }}>Clear PIN</Button>}
          </div>
          <Toggle focusKey="ud:plex" label="Link to Plex account" testId="user-plex" on={!!u.plex_account_linked}
            hint="Resume points sync both ways with the Plex account this server uses. One person only: linking them unlinks whoever was linked."
            onChange={(on) => apply({ plex_account_linked: on }, on ? `${u.display_name} is linked to the Plex account.` : 'Plex link removed.')} />
        </FocusGroup>
      </section>

      <section className="panel flex flex-col gap-3" data-testid="user-devices">
        <h2 className="heading">Devices and sessions</h2>
        {u.sessions.length ? <FocusGroup focusKey="ud:devices" className="flex flex-col gap-2">
          {u.sessions.map((s) => <DeviceRow key={s.sid} session={s} scope="ud" />)}
        </FocusGroup> : <p className="muted">Not signed in anywhere.</p>}
      </section>

      {u.history.length > 0 && (
        <section className="panel flex flex-col gap-3" data-testid="user-history">
          <h2 className="heading">Recently watched</h2>
          <FocusGroup focusKey="ud:history" className="flex flex-col gap-2">
            {u.history.slice(0, HISTORY_SHOWN).map((h) => <HistoryRow key={h.ref_id} entry={h} />)}
          </FocusGroup>
        </section>
      )}

      <FocusGroup focusKey="ud:actions" className="flex gap-3 flex-wrap">
        <Button focusKey="ud:activity" icon="list" onEnter={() => go(`/users?tab=activity&user=${encodeURIComponent(u.id)}`)}>Activity about {u.display_name}</Button>
        {u.status === 'suspended'
          ? <Button focusKey="ud:reinstate" icon="check" disabled={status.isPending} testId="user-reinstate"
            onEnter={() => status.mutate('reinstate', { onSuccess: () => { toast(`${u.display_name} can sign in again.`, 'success'); void setFocus('ud:suspend') }, onError: failed })}>Reinstate</Button>
          : <Button focusKey="ud:suspend" danger onEnter={() => setAsking('suspend')} testId="user-suspend">Suspend</Button>}
        <Button focusKey="ud:remove" danger icon="trash" onEnter={() => setAsking('remove')} testId="user-remove">Remove</Button>
      </FocusGroup>

      <Modal open={asking === 'suspend'} onClose={() => setAsking(null)} title={`Suspend ${u.display_name}?`} firstFocusKey="ask:cancel">
        <p>They are signed out everywhere and anything they are watching stops. Their history and settings are kept, and you can reinstate them at any time.</p>
        <div className="flex gap-3 flex-wrap">
          <Button focusKey="ask:cancel" onEnter={() => setAsking(null)}>Cancel</Button>
          <Button focusKey="ask:ok" danger disabled={status.isPending} testId="confirm-suspend"
            onEnter={() => status.mutate('suspend', { onSuccess: () => toast(`${u.display_name} is suspended.`, 'success'), onError: failed, onSettled: () => setAsking(null) })}>Suspend</Button>
        </div>
      </Modal>
      <Modal open={asking === 'remove'} onClose={() => setAsking(null)} title={`Remove ${u.display_name}?`} firstFocusKey="ask:cancel">
        <p>Their account, watch history, resume points, favorites and watchlist are deleted. This cannot be undone.</p>
        <div className="flex gap-3 flex-wrap">
          <Button focusKey="ask:cancel" onEnter={() => setAsking(null)}>Keep {u.display_name}</Button>
          <Button focusKey="ask:ok" danger icon="trash" disabled={remove.isPending} testId="confirm-remove"
            onEnter={() => remove.mutate(undefined, { onSuccess: () => { toast(`${u.display_name} was removed.`, 'success'); navigate('/users', { replace: true }) }, onError: (e) => { setAsking(null); failed(e) } })}>Remove</Button>
        </div>
      </Modal>
      <PinDialog open={asking === 'pin'} onClose={() => setAsking(null)} title={`New PIN for ${u.display_name}`} hint="Four to eight digits." confirm submitLabel="Save PIN"
        onSubmit={async (value) => { await pin.mutateAsync(value); toast('PIN saved.', 'success'); setAsking(null) }} />
    </div>
  )
}

/**
 * One signed-in device: what it is, when it was last seen, Revoke, and for a holder of users.manage
 * the "shared household device" switch. `rename` adds Rename, for a person's own devices. The switch
 * and Rename use a call older servers lack; the first 404 hides both for the rest of the visit.
 */
export function DeviceRow({ session: s, scope, rename, onRevoked }: { session: AuthSession; scope: string; rename?: () => void; onRevoked?: (s: AuthSession) => void }) {
  const toast = useUi((st) => st.toast)
  const manages = useCan('users.manage')
  const editable = !useAbsent((a) => a.sessionEdit)
  const markAbsent = useAbsent((a) => a.markSessionEdit)
  const revoke = usePeopleChange(() => people.revokeSession(s.sid))
  const share = usePeopleChange((on: boolean) => people.patchSession(s.sid, { shared_device: on }))
  const name = deviceName(s.label, s.kind, s.user_agent)
  return (
    <div className="person-row device-row" data-testid="device-row" data-current={s.current}>
      <span className="who">
        <span className="flex items-center gap-2 flex-wrap"><strong>{name}</strong>{s.current && <span className="badge badge-ok">This device</span>}{s.shared_device && <span className="badge">Shared</span>}</span>
        <span className="label">{[DEVICE_KIND[s.kind] ?? s.kind, `Last seen ${ago(s.last_seen_at)}`, `Signed in ${ago(s.created_at)}`].join(' · ')}</span>
      </span>
      {manages && editable && <Chip focusKey={`${scope}:dev:${s.sid}:shared`} pressed={s.shared_device} label={`${name}: shared household device, ${s.shared_device ? 'on' : 'off'}`} testId="device-shared"
        onEnter={() => share.mutate(!s.shared_device, { onError: (e) => { if (absent(e)) { markAbsent(); toast('This server cannot change that yet.', 'error') } else toast(e.message, 'error') } })}>Shared household device</Chip>}
      {rename && editable && <Button focusKey={`${scope}:dev:${s.sid}:rename`} label={`Rename ${name}`} onEnter={rename} testId="device-rename">Rename</Button>}
      <Button focusKey={`${scope}:dev:${s.sid}:revoke`} danger label={`Sign out ${name}`} disabled={revoke.isPending} testId="device-revoke"
        onEnter={() => revoke.mutate(undefined, { onSuccess: () => { toast(`${name} was signed out.`, 'success'); onRevoked?.(s) }, onError: (e) => toast(e.message, 'error') })}>Revoke</Button>
    </div>
  )
}

function HistoryRow({ entry: h }: { entry: Detail['history'][number] }) {
  const go = useGo()
  const title = useTitle(h.ref_id)
  const done = h.completed ? 'Finished' : h.duration_ms ? `${Math.round((h.position_ms / h.duration_ms) * 100)}% watched` : null
  const name = title.data?.title ?? h.ref_id
  return (
    <Focusable focusKey={`ud:history:${h.ref_id}`} className="person-row person-link history-row fx-flat" role="link" label={`${name}, ${ago(h.updated_at)}`} data-testid="history-row"
      onEnter={() => go(`/title/${encodeURIComponent(h.ref_id)}`)}>
      <span className="who"><strong className="truncate">{name}</strong><span className="label">{[h.kind, done, ago(h.updated_at)].filter(Boolean).join(' · ')}</span></span>
      <Icon name="chevR" size={18} />
    </Focusable>
  )
}
