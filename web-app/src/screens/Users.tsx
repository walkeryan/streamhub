import { useEffect, useState } from 'react'
import { Navigate, useSearchParams } from 'react-router'
import { useCan, useMe } from '../api/me'
import {
  people, useAudit, useInvites, usePeopleChange, useRoles, useUsers,
  type Invite, type InviteCreated, type Role, type UserListItem,
} from '../api/people'
import type { components } from '../api/schema'
import { Avatar, AVATARS } from '../components/Avatar'
import { CatalogPicker } from '../components/CatalogPicker'
import { Icon } from '../components/Icon'
import { LimitsEditor, PermissionSwitches, Toggle } from '../components/PeopleControls'
import { Button, Chip, Empty, ErrorState, Modal, Skeleton } from '../components/ui'
import { FocusGroup, Focusable, FocusableInput } from '../nav/Focusable'
import { useGo, useRestoreFocus } from '../nav/back'
import { doesFocusableExist, getCurrentFocusKey, setFocus } from '../nav/spatial'
import { PERMISSION_COUNT, SIGN_IN, STATUS, ago, auditLine, limitsSummary, type Limits } from '../lib/people'
import { usePhone } from '../lib/layout'
import { useUi } from '../store/ui'

type Permission = components['schemas']['Permission']
const TABS = [['people', 'People'], ['roles', 'Roles'], ['invites', 'Invites'], ['activity', 'Activity']] as const
type Tab = (typeof TABS)[number][0]

/** Everyone who can use this StreamHub, what each may do, and who changed what. Needs users.manage. */
export function Users() {
  const allowed = useCan('users')
  const [sp, setSp] = useSearchParams()
  const tab = (TABS.find((t) => t[0] === sp.get('tab'))?.[0] ?? 'people') as Tab
  const users = useUsers()
  const roles = useRoles(allowed)
  const ready = tab === 'people' ? !!users.data && !!roles.data : tab === 'roles' ? !!roles.data : true
  useRestoreFocus(ready, `users:tab:${tab}`)
  // Coming back to People shows who has joined or signed in meanwhile, without waiting for the next poll.
  const refetchUsers = users.refetch
  useEffect(() => { if (tab === 'people') void refetchUsers() }, [tab, refetchUsers])
  if (!allowed) return <Navigate to="/" replace />
  const failed = users.error ?? roles.error
  return (
    <div className="page-pad flex flex-col gap-6" data-testid="users">
      <h1 className="display">Users</h1>
      <FocusGroup focusKey="users:tabs" className="chip-scroll" role="tablist">
        {TABS.map(([id, label]) => <Chip key={id} focusKey={`users:tab:${id}`} pressed={tab === id} onEnter={() => setSp(id === 'people' ? {} : { tab: id }, { replace: true })} testId={`users-tab-${id}`}>{label}</Chip>)}
      </FocusGroup>
      <div style={{ maxWidth: '64rem' }}>
        {failed ? <ErrorState error={failed} onRetry={() => { void users.refetch(); void roles.refetch() }} />
          : !users.data || !roles.data ? <Skeleton style={{ height: '14rem' }} />
          : tab === 'people' ? <PeopleTab users={users.data} roles={roles.data} />
          : tab === 'roles' ? <RolesTab roles={roles.data} />
          : tab === 'invites' ? <InvitesTab roles={roles.data} />
          : <ActivityTab users={users.data} roles={roles.data} />}
      </div>
    </div>
  )
}

/* ------------------------------ people ------------------------------ */
function PeopleTab({ users, roles }: { users: UserListItem[]; roles: Role[] }) {
  const me = useMe().data
  const go = useGo()
  const phone = usePhone()
  const toast = useUi((s) => s.toast)
  const approve = usePeopleChange((v: { user: UserListItem; role: Role }) => people.approve(v.user.id, v.role.id))
  const deny = usePeopleChange((user: UserListItem) => people.deny(user.id))
  const [chosen, setChosen] = useState<Record<string, string>>({})
  const [choosing, setChoosing] = useState<UserListItem | null>(null)
  const [denying, setDenying] = useState<UserListItem | null>(null)
  const [adding, setAdding] = useState(false)
  const pending = users.filter((u) => u.status === 'pending')
  const others = users.filter((u) => u.status !== 'pending')
  // Family is what the server gives a managed user it is told nothing about; the owner can pick another per person.
  const fallback = roles.find((r) => r.id === 'family') ?? roles.find((r) => r.editable) ?? roles[0]!
  const roleFor = (u: UserListItem) => roles.find((r) => r.id === chosen[u.id]) ?? fallback
  // A decided row leaves the queue; the remote must not be left pointing at it.
  useEffect(() => { if (!doesFocusableExist(getCurrentFocusKey()) && !document.querySelector('.scrim')) void setFocus('users:add') }, [users])

  const decide = (u: UserListItem, role: Role | null) => {
    const done = { onSuccess: () => toast(role ? `${u.display_name} can now sign in as ${role.name}.` : `Declined ${u.display_name}.`, 'success'), onError: (e: Error) => toast(e.message, 'error') }
    if (role) approve.mutate({ user: u, role }, done); else deny.mutate(u, done)
  }
  return (
    <div className="flex flex-col gap-6">
      {pending.length > 0 && (
        <section className="flex flex-col gap-3" data-testid="pending-queue">
          <h2 className="heading">Waiting for approval · {pending.length}</h2>
          <FocusGroup focusKey="users:pending" className="flex flex-col gap-2">
            {pending.map((u) => (
              <div key={u.id} className="panel person-row" data-testid="pending-row">
                <Avatar name={u.display_name} avatar={u.avatar} picture={u.picture} />
                <span className="who">
                  <strong className="truncate">{u.display_name}</strong>
                  <span className="label truncate">{[u.email, `Asked ${ago(u.created_at)}`].filter(Boolean).join(' · ')}</span>
                </span>
                {/* a phone has room for the three words, not for their icons as well */}
                <span className="row-actions">
                  <Button focusKey={`pending:${u.id}:approve`} primary icon={phone ? undefined : 'check'} disabled={approve.isPending || deny.isPending} onEnter={() => decide(u, roleFor(u))} testId="pending-approve">Approve as {roleFor(u).name}</Button>
                  <Button focusKey={`pending:${u.id}:role`} label={`Choose another role for ${u.display_name}`} onEnter={() => setChoosing(u)} testId="pending-role">Role</Button>
                  <Button focusKey={`pending:${u.id}:deny`} danger icon={phone ? undefined : 'x'} label={`Deny ${u.display_name}`} onEnter={() => setDenying(u)} testId="pending-deny">Deny</Button>
                </span>
              </div>
            ))}
          </FocusGroup>
        </section>
      )}
      <section className="flex flex-col gap-3">
        <div className="flex items-center gap-3 flex-wrap">
          <h2 className="heading flex-1">People · {others.length}</h2>
          <Button focusKey="users:add" icon="plus" onEnter={() => setAdding(true)} testId="users-add">Add managed user</Button>
        </div>
        <FocusGroup focusKey="users:list" className="flex flex-col gap-2">
          {others.map((u) => (
            <Focusable key={u.id} focusKey={`user:${u.id}`} className="panel person-row person-link fx-flat" role="link" label={`${u.display_name}, ${u.role_name ?? 'no role'}, ${STATUS[u.status] ?? u.status}`}
              data-testid="user-row" onEnter={() => go(`/users/${encodeURIComponent(u.id)}`)}>
              <Avatar name={u.display_name} avatar={u.avatar} picture={u.picture} />
              <span className="who">
                <span className="flex items-center gap-2 min-w-0"><strong className="truncate">{u.display_name}</strong>{u.id === me?.user.id && <span className="badge">You</span>}</span>
                <span className="label">{[`${SIGN_IN[u.kind] ?? u.kind} sign-in`, u.role_name ?? 'No role', `Last seen ${ago(u.last_seen_at)}`].join(' · ')}</span>
                {u.now_playing.length > 0 && <span className="label flex items-center gap-1 min-w-0" data-testid="now-playing" style={{ color: 'var(--t-text)' }}>
                  <Icon name="play" size={12} /><span className="truncate">Watching {u.now_playing.map((n) => n.title ?? 'something').join(', ')}</span>
                </span>}
              </span>
              <span className="row-end"><span className={`badge ${u.status === 'active' ? 'badge-ok' : 'badge-warn'}`}>{STATUS[u.status] ?? u.status}</span><Icon name="chevR" size={18} /></span>
            </Focusable>
          ))}
        </FocusGroup>
      </section>

      <Modal open={!!choosing} onClose={() => setChoosing(null)} title={`Role for ${choosing?.display_name ?? ''}`} firstFocusKey={`as:${choosing ? roleFor(choosing).id : ''}`}>
        <p className="muted">They get this role's permissions and limits when you approve them. You can change it later.</p>
        <FocusGroup focusKey="as:list" className="flex flex-col gap-2">
          {roles.map((r) => (
            <Button key={r.id} focusKey={`as:${r.id}`} pressed={!!choosing && roleFor(choosing).id === r.id} onEnter={() => { if (choosing) setChosen({ ...chosen, [choosing.id]: r.id }); setChoosing(null) }}>{r.name}</Button>
          ))}
        </FocusGroup>
      </Modal>
      <Modal open={!!denying} onClose={() => setDenying(null)} title={`Deny ${denying?.display_name ?? ''}?`} firstFocusKey="deny:cancel">
        <p>Their request is removed. They can ask again by signing in.</p>
        <div className="flex gap-3 flex-wrap">
          <Button focusKey="deny:cancel" onEnter={() => setDenying(null)}>Keep waiting</Button>
          <Button focusKey="deny:ok" danger icon="x" onEnter={() => { const u = denying; setDenying(null); if (u) decide(u, null) }}>Deny</Button>
        </div>
      </Modal>
      {adding && <AddManagedUser roles={roles} fallback={fallback} onClose={() => setAdding(false)} />}
    </div>
  )
}

function AddManagedUser({ roles, fallback, onClose }: { roles: Role[]; fallback: Role; onClose: () => void }) {
  const toast = useUi((s) => s.toast)
  const create = usePeopleChange(people.create)
  const [name, setName] = useState('')
  const [avatar, setAvatar] = useState<string>(AVATARS[0])
  const [role, setRole] = useState(fallback.id)
  const [pin, setPin] = useState('')
  const [error, setError] = useState<string | null>(null)
  const submit = () => {
    if (!name.trim()) { setError('Give them a name.'); return }
    if (pin && !/^\d{4,8}$/.test(pin)) { setError('A PIN is 4 to 8 digits.'); return }
    setError(null)
    create.mutate({ name: name.trim(), avatar, role_id: role, pin: pin || null }, {
      onSuccess: (user) => { toast(`Added ${user.display_name}.`, 'success'); onClose() },
      onError: (e) => setError(e.message),
    })
  }
  return (
    <Modal open onClose={onClose} title="Add a managed user" firstFocusKey="add:name" wide testId="add-user">
      <p className="muted">For someone without a Google account, a child for instance. They appear on shared household devices and nowhere else.</p>
      <FocusableInput focusKey="add:name" label="Name" placeholder="Name" maxLength={64} value={name} onChange={setName} onSubmit={submit} />
      <section className="flex flex-col gap-2"><h3 className="label">Picture</h3>
        <FocusGroup focusKey="add:avatars" className="flex gap-2 flex-wrap" role="group" label="Picture">
          {AVATARS.map((a) => (
            <Focusable key={a} focusKey={`add:avatar:${a}`} className="avatar-pick" aria-pressed={avatar === a} label={`Picture: ${a}`} onEnter={() => setAvatar(a)}><Avatar avatar={a} size={3} /></Focusable>
          ))}
        </FocusGroup>
      </section>
      <section className="flex flex-col gap-2"><h3 className="label">Role</h3>
        <FocusGroup focusKey="add:roles" className="flex gap-2 flex-wrap" role="group" label="Role">
          {roles.filter((r) => r.editable).map((r) => <Chip key={r.id} focusKey={`add:role:${r.id}`} pressed={role === r.id} onEnter={() => setRole(r.id)}>{r.name}</Chip>)}
        </FocusGroup>
      </section>
      <section className="flex flex-col gap-2"><h3 className="label">PIN (optional)</h3>
        <div style={{ width: '14rem', maxWidth: '100%' }}>
          <FocusableInput focusKey="add:pin" label="PIN, optional" type="password" inputMode="numeric" maxLength={8} placeholder="4 to 8 digits" value={pin} onChange={(v) => setPin(v.replace(/\D/g, ''))} onSubmit={submit} />
        </div>
        <p className="label">With a PIN, nobody else on a shared device can switch to them.</p>
      </section>
      {error && <p role="alert" style={{ color: 'var(--t-danger)' }}>{error}</p>}
      <div className="flex gap-3 flex-wrap">
        <Button focusKey="add:ok" primary icon="plus" disabled={create.isPending} onEnter={submit} testId="add-user-ok">Add user</Button>
        <Button focusKey="add:cancel" onEnter={onClose}>Cancel</Button>
      </div>
    </Modal>
  )
}

/* ------------------------------ roles ------------------------------ */
const NEW_ROLE: { permissions: Permission[]; limits: Limits } = {
  permissions: ['watch.library', 'watch.vod', 'watch.live', 'rooms.join', 'requests.create'],
  limits: { max_streams: null, rating_ceiling_movie: null, rating_ceiling_tv: null, allow_unrated: true },
}

function RolesTab({ roles }: { roles: Role[] }) {
  const toast = useUi((s) => s.toast)
  const [editing, setEditing] = useState<Role | 'new' | null>(null)
  const [removing, setRemoving] = useState<Role | null>(null)
  const remove = usePeopleChange((role: Role) => people.removeRole(role.id))
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3 flex-wrap">
        <p className="muted flex-1" style={{ minWidth: '14rem' }}>A role is a set of permissions and limits. Changing one reaches everyone who holds it on their next request.</p>
        <Button focusKey="roles:new" icon="plus" onEnter={() => setEditing('new')} testId="role-new">New role</Button>
      </div>
      <FocusGroup focusKey="roles:list" className="flex flex-col gap-2">
        {roles.map((r) => (
          <div key={r.id} className="panel person-row" data-testid="role-row">
            <span className="who">
              <span className="flex items-center gap-2 flex-wrap"><strong>{r.name}</strong><span className="badge">{r.builtin ? 'Built in' : 'Custom'}</span><span className="label">{r.user_count} {r.user_count === 1 ? 'person' : 'people'}</span></span>
              <span className="label">{r.permissions.length} of {PERMISSION_COUNT} permissions · {limitsSummary(r.limits)}</span>
            </span>
            <Button focusKey={`role:${r.id}`} label={`${r.editable ? 'Edit' : 'View'} the ${r.name} role`} onEnter={() => setEditing(r)} testId={`role-edit-${r.id}`}>{r.editable ? 'Edit' : 'View'}</Button>
          </div>
        ))}
      </FocusGroup>
      {editing && <RoleEditor key={editing === 'new' ? 'new' : editing.id} role={editing === 'new' ? null : editing} onClose={() => setEditing(null)}
        onRemove={(role) => { setEditing(null); setRemoving(role) }} />}
      {/* Asked in a dialog of its own, opening on the safe answer: a question swapped in under a parked cursor would hand it the dangerous one. */}
      <Modal open={!!removing} onClose={() => setRemoving(null)} title={`Delete the ${removing?.name ?? ''} role?`} firstFocusKey="role:keep">
        <p>Only a role nobody holds can be deleted.</p>
        <div className="flex gap-3 flex-wrap">
          <Button focusKey="role:keep" onEnter={() => setRemoving(null)}>Keep role</Button>
          <Button focusKey="role:remove-ok" danger icon="trash" disabled={remove.isPending} testId="confirm-role-delete"
            onEnter={() => { const role = removing; if (role) remove.mutate(role, { onSuccess: () => { toast(`${role.name} deleted.`, 'success'); void setFocus('roles:new') }, onError: (e) => toast(e.message, 'error'), onSettled: () => setRemoving(null) }) }}>Delete role</Button>
        </div>
      </Modal>
    </div>
  )
}

function RoleEditor({ role, onClose, onRemove }: { role: Role | null; onClose: () => void; onRemove: (role: Role) => void }) {
  const toast = useUi((s) => s.toast)
  const [name, setName] = useState(role?.name ?? '')
  const [permissions, setPermissions] = useState<Permission[]>(role?.permissions ?? NEW_ROLE.permissions)
  const [limits, setLimits] = useState<Limits>(role?.limits ?? NEW_ROLE.limits)
  const [shared, setShared] = useState(role?.on_shared_devices ?? true)
  const [error, setError] = useState<string | null>(null)
  const save = usePeopleChange(() => {
    const body = { permissions, limits, on_shared_devices: shared }
    return role ? people.patchRole(role.id, role.builtin ? body : { ...body, name: name.trim() }) : people.createRole({ ...body, name: name.trim() })
  })
  const fixed = !!role && !role.editable
  const submit = () => {
    if (!role?.builtin && !name.trim()) { setError('Give the role a name.'); return }
    setError(null)
    save.mutate(undefined, { onSuccess: (saved) => { toast(`${saved.name} saved.`, 'success'); onClose() }, onError: (e) => setError(e.message) })
  }
  return (
    <Modal open onClose={onClose} title={role ? `${role.name} role` : 'New role'} firstFocusKey={fixed ? 'role:close' : role?.builtin ? 'role:edit:watch.library' : 'role:name'} wide testId="role-editor">
      {fixed ? <p className="muted">Owner always has every permission and no limits. It cannot be edited or removed, and there is always at least one.</p>
        : !role?.builtin && <FocusableInput focusKey="role:name" label="Role name" placeholder="Role name" maxLength={64} value={name} onChange={setName} />}
      <PermissionSwitches scope="role:edit" held={permissions}
        onChange={fixed ? undefined : (id, on) => setPermissions(on ? [...permissions, id] : permissions.filter((p) => p !== id))} />
      {!fixed && <>
        <h3 className="heading">Limits</h3>
        <LimitsEditor scope="role:limit" limits={limits} onChange={(change) => setLimits({ ...limits, ...change })} />
        <Toggle focusKey="role:shared" label="Show on shared devices" hint={'People with this role are offered on a household device\'s "Who\'s watching?"'} on={shared} onChange={setShared} />
      </>}
      {error && <p role="alert" style={{ color: 'var(--t-danger)' }}>{error}</p>}
      <FocusGroup focusKey="role:actions" className="flex gap-3 flex-wrap">
        {!fixed && <Button focusKey="role:save" primary disabled={save.isPending} onEnter={submit} testId="role-save">{role ? 'Save role' : 'Create role'}</Button>}
        <Button focusKey="role:close" onEnter={onClose}>{fixed ? 'Close' : 'Cancel'}</Button>
        {role && !role.builtin && <Button focusKey="role:remove" danger icon="trash" onEnter={() => onRemove(role)}>Delete role</Button>}
      </FocusGroup>
    </Modal>
  )
}

/* ------------------------------ invites ------------------------------ */
const EXPIRY = [[24, '1 day'], [72, '3 days'], [168, '1 week'], [720, '30 days']] as const
const USES = [1, 2, 5, 10]
const INVITE_STATE: Record<Invite['state'], string> = { open: 'Open', used: 'Used up', expired: 'Expired', revoked: 'Revoked' }

function InvitesTab({ roles }: { roles: Role[] }) {
  const invites = useInvites()
  const toast = useUi((s) => s.toast)
  const offered = roles.filter((r) => r.editable)
  const [role, setRole] = useState(offered.find((r) => r.id === 'friend')?.id ?? offered[0]?.id ?? '')
  const [hours, setHours] = useState<number>(72)
  const [uses, setUses] = useState(1)
  const [made, setMade] = useState<InviteCreated | null>(null)
  const create = usePeopleChange(people.invite)
  const revoke = usePeopleChange((invite: Invite) => people.revokeInvite(invite.id))
  const link = made ? new URL(made.url, window.location.origin).href : ''
  const copy = async () => {
    try { await navigator.clipboard.writeText(link); toast('Link copied.', 'success') } catch { toast('Copying is blocked here. Select the link and copy it.', 'error'); void setFocus('invite:link') }
  }
  return (
    <div className="flex flex-col gap-6">
      <section className="panel flex flex-col gap-4" data-testid="invite-form">
        <h2 className="heading">New invite link</h2>
        <p className="muted">Whoever opens the link and signs in with Google becomes a user at once, with the role you pick. No approval step.</p>
        <section className="flex flex-col gap-2"><h3 className="label">Role</h3>
          <FocusGroup focusKey="invite:roles" className="flex gap-2 flex-wrap" role="group" label="Role">
            {offered.map((r) => <Chip key={r.id} focusKey={`invite:role:${r.id}`} pressed={role === r.id} onEnter={() => setRole(r.id)}>{r.name}</Chip>)}
          </FocusGroup>
        </section>
        <section className="flex flex-col gap-2"><h3 className="label">Expires after</h3>
          <FocusGroup focusKey="invite:expiry" className="flex gap-2 flex-wrap" role="group" label="Expires after">
            {EXPIRY.map(([h, text]) => <Chip key={h} focusKey={`invite:hours:${h}`} pressed={hours === h} onEnter={() => setHours(h)}>{text}</Chip>)}
          </FocusGroup>
        </section>
        <section className="flex flex-col gap-2"><h3 className="label">Can be used</h3>
          <FocusGroup focusKey="invite:uses" className="flex gap-2 flex-wrap" role="group" label="Can be used">
            {USES.map((n) => <Chip key={n} focusKey={`invite:uses:${n}`} pressed={uses === n} onEnter={() => setUses(n)}>{n === 1 ? 'Once' : `${n} times`}</Chip>)}
          </FocusGroup>
        </section>
        <div><Button focusKey="invite:create" primary icon="link" disabled={create.isPending || !role} testId="invite-create"
          onEnter={() => create.mutate({ role_id: role, expires_in_hours: hours, max_uses: uses }, { onSuccess: (invite) => { setMade(invite); window.setTimeout(() => void setFocus('invite:copy'), 30) }, onError: (e) => toast(e.message, 'error') })}>Create invite link</Button></div>
        {made && (
          <div className="flex flex-col gap-2" data-testid="invite-made" role="status">
            <p>Send this link to the person you are inviting. It is shown only now.</p>
            <div className="flex gap-2 flex-wrap items-center">
              <div style={{ flex: '1 1 16rem', minWidth: 0 }}><FocusableInput focusKey="invite:link" label="Invite link" value={link} onChange={() => undefined} readOnly /></div>
              <Button focusKey="invite:copy" icon="copy" onEnter={() => void copy()} testId="invite-copy">Copy link</Button>
            </div>
          </div>
        )}
      </section>
      <section className="flex flex-col gap-3">
        <h2 className="heading">Invites</h2>
        {invites.error ? <ErrorState error={invites.error} onRetry={() => void invites.refetch()} /> : !invites.data ? <Skeleton style={{ height: '8rem' }} /> : !invites.data.length ? <p className="muted">No invites yet.</p> : (
          <FocusGroup focusKey="invite:list" className="flex flex-col gap-2">
            {invites.data.map((i) => {
              const about = <span className="who">
                <span className="flex items-center gap-2 flex-wrap"><strong>{i.role_name ?? i.role_id}</strong><span className={`badge ${i.state === 'open' ? 'badge-ok' : ''}`}>{INVITE_STATE[i.state]}</span></span>
                <span className="label">{[`Used ${i.uses} of ${i.max_uses}`, `${Date.parse(i.expires_at) > Date.now() ? 'Expires' : 'Expired'} ${new Date(i.expires_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}`, i.created_by_name ? `By ${i.created_by_name}` : null].filter(Boolean).join(' · ')}</span>
              </span>
              // A spent invite has nothing to press, but a remote still has to be able to walk down the list.
              return i.state === 'open' ? (
                <div key={i.id} className="panel person-row" data-testid="invite-row" data-state={i.state}>
                  {about}
                  <Button focusKey={`invite:${i.id}:revoke`} danger label={`Revoke the ${i.role_name ?? ''} invite`} testId="invite-revoke"
                    onEnter={() => { void setFocus('invite:create'); revoke.mutate(i, { onSuccess: () => toast('Invite revoked.', 'success'), onError: (e) => toast(e.message, 'error') }) }}>Revoke</Button>
                </div>
              ) : (
                <Focusable key={i.id} focusKey={`invite:${i.id}`} className="panel person-row fx-flat" role="listitem" label={`${i.role_name ?? i.role_id} invite, ${INVITE_STATE[i.state]}`} data-testid="invite-row" data={{ state: i.state }}>{about}</Focusable>
              )
            })}
          </FocusGroup>
        )}
      </section>
    </div>
  )
}

/* ------------------------------ activity ------------------------------ */
function ActivityTab({ users, roles }: { users: UserListItem[]; roles: Role[] }) {
  const [sp, setSp] = useSearchParams()
  const about = sp.get('user') ?? ''
  const log = useAudit(about || undefined)
  const go = useGo()
  const roleName = (id: string) => roles.find((r) => r.id === id)?.name ?? id
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3 flex-wrap">
        <CatalogPicker scope="audit:user" label="About" value={about} options={[{ id: '', label: 'Everyone' }, ...users.map((u) => ({ id: u.id, label: u.display_name }))]} onChange={(id) => setSp(id ? { tab: 'activity', user: id } : { tab: 'activity' }, { replace: true })} />
        <p className="label">Who changed what about whom, newest first.</p>
      </div>
      {log.error ? <ErrorState error={log.error} onRetry={() => void log.refetch()} /> : !log.data ? <Skeleton style={{ height: '10rem' }} /> : !log.data.length ? (
        <Empty title="Nothing recorded" hint={about ? 'No changes about or by this person yet.' : 'Changes to users, roles, invites and devices are listed here.'} />
      ) : (
        <FocusGroup focusKey="audit:list" className="flex flex-col gap-2" role="list" data-testid="audit-list">
          {log.data.map((e) => {
            const person = e.target_user_id && users.some((u) => u.id === e.target_user_id && u.status !== 'pending') ? e.target_user_id : null
            return (
              <Focusable key={e.id} focusKey={`audit:${e.id}`} className="panel audit-row fx-flat" role={person ? 'link' : 'listitem'} label={auditLine(e, roleName)} data={{ action: e.action }}
                onEnter={person ? () => go(`/users/${encodeURIComponent(person)}`) : undefined}>
                <span className="flex-1 min-w-0">{auditLine(e, roleName)}</span>
                <time className="label" dateTime={e.at} title={new Date(e.at).toLocaleString()}>{ago(e.at)}</time>
              </Focusable>
            )
          })}
        </FocusGroup>
      )}
    </div>
  )
}
