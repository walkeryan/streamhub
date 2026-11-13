import { http, HttpResponse } from 'msw'
import type { components } from '../api/schema'
import type { Profile } from '../api/types'
import { profiles, progress } from './fixtures'

type S = components['schemas']
type Permission = S['Permission']
type Limits = S['Limits']
type User = S['User']
type Method = S['SignInMethod']

const B = '/api/v1'
const KEY = 'mock.people.v1'
const ALL: Permission[] = [
  'watch.library', 'watch.vod', 'watch.live', 'watch.adult', 'rooms.join', 'rooms.create', 'recordings.view', 'recordings.schedule',
  'requests.create', 'requests.auto_approve', 'requests.approve', 'remote.access', 'users.invite', 'users.manage', 'sources.manage', 'system.manage',
]
const WATCH: Permission[] = ['watch.library', 'watch.vod', 'watch.live']
const FAMILY: Permission[] = [...WATCH, 'rooms.join', 'rooms.create', 'recordings.view', 'recordings.schedule', 'requests.create', 'requests.auto_approve', 'remote.access', 'users.invite']
const OPEN: Limits = { max_streams: null, rating_ceiling_movie: null, rating_ceiling_tv: null, allow_unrated: true }
const MOVIE = ['G', 'PG', 'PG-13', 'R', 'NC-17'], TV = ['TV-Y', 'TV-Y7', 'TV-G', 'TV-PG', 'TV-14', 'TV-MA']

interface StoredUser extends User {
  grants: Permission[]; denials: Permission[]; limit_overrides: Partial<Limits>; shared_override: boolean | null
  pin: string | null; now_playing: S['NowPlaying'][]
  /** Username-and-password sign-in. The mock keeps the password as typed. */
  password?: string | null
  /** The Plex account this user signs in with (its id at plex.tv). */
  plex_account?: string | null
  /** Google sign-in linked or unlinked since; unset, someone who came in through Google has it. */
  google_linked?: boolean
  /** An approved sign-in collects its tokens once. */
  handed?: boolean
}
type StoredRole = Omit<S['Role'], 'user_count' | 'editable'>
type StoredSession = Omit<S['AuthSession'], 'current'>
interface Db {
  roles: StoredRole[]; users: StoredUser[]; profiles: Profile[]; sessions: StoredSession[]
  invites: Array<Omit<S['Invite'], 'state'> & { token: string; revoked: boolean; display_name?: string | null; user_id?: string | null }>; audit: S['AuditEntry'][]
  /** Profiles whose token predates a change to their access: their next call answers 401 session_stale. */
  stale: string[]
  /** Profiles whose session on this device was signed out elsewhere: every call, and the refresh, answers 401 session_revoked. */
  revoked?: string[]
  fails: Record<string, { count: number; until: number }>
  adult: Record<string, number>
  /** This browser stands in for one device; these are its auth_session's own fields. */
  shared: boolean; label: string | null
  seq: number
}

const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString()
const person = (id: string, n: number, name: string, kind: User['kind'], role: string | null, more: Partial<StoredUser> = {}): StoredUser => ({
  id, kind, display_name: name, email: kind === 'google' ? `${name.split(' ')[0]!.toLowerCase()}@example.com` : null, picture: null, avatar: null,
  status: 'active', role_id: role, profile_id: `p${n}`, has_pin: false, on_shared_devices: false, plex_account_linked: false,
  created_at: ago(60 * 24 * 40), approved_at: ago(60 * 24 * 39), last_seen_at: ago(60 * 26), grants: [], denials: [], limit_overrides: {}, shared_override: null,
  pin: null, now_playing: [], ...more,
})
const profileFor = (u: StoredUser): Profile => ({
  id: u.profile_id!, name: u.display_name, avatar: u.avatar ?? null, is_admin: false, theme_id: 'midnight', languages: ['eng'], has_pin: !!u.pin, kids: u.role_id === 'kid', disclaimer_accepted_version: null,
})

function seed(): Db {
  const role = (id: string, name: string, permissions: Permission[], limits: Partial<Limits> = {}, shared = true, builtin = true): StoredRole =>
    ({ id, name, builtin, permissions, limits: { ...OPEN, ...limits }, on_shared_devices: shared, updated_at: ago(60 * 24 * 30) })
  const users = [
    person('u1', 1, profiles[0]!.name, 'google', 'owner', { email: 'owner@example.com', last_seen_at: ago(1), plex_account_linked: true }),
    person('u2', 2, 'Guest', 'managed', 'family', { last_seen_at: ago(190) }),
    person('u3', 3, 'Kids', 'managed', 'kid', { pin: '1234', avatar: 'fox' }),
    person('u4', 4, 'Priya', 'managed', 'family', { pin: '4321', avatar: 'owl', last_seen_at: ago(60 * 50) }),
    person('u5', 5, 'Mara Ellison', 'google', 'friend', { last_seen_at: ago(2), now_playing: [{ id: 'tmdb:movie:103', title: 'Neon Cartographers' }], limit_overrides: { max_streams: 2 }, grants: ['recordings.view'] }),
    person('u6', 6, 'Theo Okafor', 'google', 'family', { status: 'suspended', last_seen_at: ago(60 * 24 * 12) }),
    person('u7', 7, 'Junko Hale', 'google', null, { status: 'pending', approved_at: null, last_seen_at: null, created_at: ago(95) }),
    person('u8', 8, 'Luis Ferreira', 'google', null, { status: 'pending', approved_at: null, last_seen_at: null, created_at: ago(12) }),
  ]
  const device = (sid: string, user: string, kind: 'browser' | 'tv', label: string | null, agent: string, seen: number, shared = false): StoredSession =>
    ({ sid, user_id: user, kind, label, user_agent: agent, ip: null, shared_device: shared, created_at: ago(60 * 24 * 20), last_seen_at: ago(seen) })
  const invite = (id: string, roleId: string, made: number, hours: number, uses: number) => ({
    id, token: id.padEnd(43, 'x'), role_id: roleId, role_name: null, created_by_user_id: 'u1', created_by_name: users[0]!.display_name,
    created_at: ago(made), expires_at: new Date(Date.now() - made * 60_000 + hours * 3600_000).toISOString(), max_uses: 1, uses, revoked: false,
  })
  const entry = (id: number, minutes: number, action: string, target: StoredUser | null, detail: Record<string, unknown>): S['AuditEntry'] =>
    ({ id, at: ago(minutes), action, actor_user_id: 'u1', actor_name: users[0]!.display_name, target_user_id: target?.id ?? null, target_name: target?.display_name ?? null, detail })
  return {
    roles: [
      role('owner', 'Owner', ALL),
      role('family', 'Family', FAMILY),
      role('kid', 'Kid', [...WATCH, 'rooms.join', 'recordings.view', 'requests.create'], { max_streams: 1, rating_ceiling_movie: 'PG', rating_ceiling_tv: 'TV-PG', allow_unrated: false }),
      role('friend', 'Friend', [...WATCH, 'rooms.join', 'rooms.create', 'requests.create', 'remote.access'], { max_streams: 1 }, false),
      role('manager', 'Manager', [...FAMILY, 'requests.approve', 'users.manage'], {}, true, false),
    ],
    users, profiles: users.slice(3).map(profileFor),
    sessions: [
      device('tv-den', 'u1', 'tv', 'Living room TV', 'StreamHub TV/1.0 (Android 12)', 35, true),
      device('ph-owner', 'u1', 'browser', null, 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 Mobile Safari/604.1', 60 * 9),
      device('tab-guest', 'u2', 'browser', 'Kitchen tablet', 'Mozilla/5.0 (Linux; Android 14; Tablet) AppleWebKit/537.36 Chrome/141.0 Safari/537.36', 190),
      device('lap-mara', 'u5', 'browser', null, 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/141.0 Safari/537.36', 2),
    ],
    invites: [invite('inv-open', 'friend', 60 * 20, 72, 0), invite('inv-used', 'family', 60 * 24 * 9, 72, 1), invite('inv-old', 'friend', 60 * 24 * 30, 72, 0)],
    audit: [
      entry(5, 60 * 20, 'invite.create', null, { role_id: 'friend' }),
      entry(4, 60 * 24 * 3, 'user.access_change', users[4]!, { after: { limits: { max_streams: 2 } } }),
      entry(3, 60 * 24 * 12, 'user.suspend', users[5]!, {}),
      entry(2, 60 * 24 * 30, 'role.update', null, { role_id: 'kid' }),
      entry(1, 60 * 24 * 39, 'user.approve', users[4]!, { role_id: 'friend' }),
    ],
    stale: [], fails: {}, adult: {}, shared: localStorage.getItem('mock.device') === 'shared', label: null, seq: 20,
  }
}

// Kept in localStorage, unlike the other fixtures: an owner's tab and the tab of the person being
// approved, switched or suspended then see one household.
function load(): Db {
  try {
    const stored = JSON.parse(localStorage.getItem(KEY) ?? 'null') as Db | null
    if (stored) return stored
  } catch { /* seeded again below */ }
  const db = seed()
  save(db)
  return db
}
const save = (db: Db) => localStorage.setItem(KEY, JSON.stringify(db))
/** Stand-ins for a server that lacks these calls: `legacy` has no users at all, `no-switch` lacks the two that hand a shared device over. */
const mode = () => localStorage.getItem('mock.people.mode')

const error = (status: number, code: string, message: string, more: Record<string, unknown> = {}, headers?: Record<string, string>) =>
  HttpResponse.json({ code, message, ...more }, { status, headers })
const gone = () => error(404, 'not_found', 'Not found')
const none = () => new HttpResponse(null, { status: 204 })
const bearer = (request: Request) => request.headers.get('authorization')?.replace('Bearer ', '') ?? ''
const callerOf = (db: Db, request: Request) => {
  const token = bearer(request)
  return token.startsWith('mock-access-') ? db.users.find((u) => u.profile_id === token.slice('mock-access-'.length)) : undefined
}
const roleOf = (db: Db, u: StoredUser) => db.roles.find((r) => r.id === u.role_id)
const permissionsOf = (db: Db, u: StoredUser): Permission[] => {
  const role = roleOf(db, u)
  if (!role || u.status !== 'active') return []
  return ALL.filter((p) => (role.permissions.includes(p) || u.grants.includes(p)) && !u.denials.includes(p))
}
const limitsOf = (db: Db, u: StoredUser): Limits => ({ ...OPEN, ...roleOf(db, u)?.limits, ...u.limit_overrides })
const sharedOf = (db: Db, u: StoredUser) => u.shared_override ?? !!roleOf(db, u)?.on_shared_devices
const owners = (db: Db) => db.users.filter((u) => u.role_id === 'owner' && u.status === 'active')
const hasGoogle = (u: StoredUser) => u.google_linked ?? u.kind === 'google'
const methodsOf = (u: StoredUser): Method[] => [hasGoogle(u) ? 'google' : null, u.plex_account ? 'plex' : null, u.username && u.password ? 'password' : null].filter((m): m is Method => !!m)
const out = (db: Db, u: StoredUser): S['User'] => {
  const { grants: _g, denials: _d, limit_overrides: _l, shared_override: _s, pin, now_playing: _n, handed: _h, password: _p, plex_account: _x, google_linked: _o, ...user } = u
  return { ...user, role_name: roleOf(db, u)?.name ?? null, has_pin: !!pin, on_shared_devices: sharedOf(db, u), username: u.username ?? null, plex_username: u.plex_account ? u.plex_username ?? null : null, sign_in_methods: methodsOf(u) }
}
const listed = (db: Db, u: StoredUser): S['UserListItem'] => ({ ...out(db, u), now_playing: u.now_playing })
const local = (db: Db, u: StoredUser): S['AuthSession'] =>
  ({ sid: 'local', user_id: u.id, kind: 'browser', label: db.label, user_agent: navigator.userAgent, ip: null, shared_device: db.shared, created_at: ago(60 * 24), last_seen_at: ago(0), current: true })
const sessionsOf = (db: Db, u: StoredUser, caller: StoredUser | undefined): S['AuthSession'][] =>
  [...(caller?.id === u.id ? [local(db, u)] : []), ...db.sessions.filter((s) => s.user_id === u.id).map((s) => ({ ...s, current: false }))]
const detail = (db: Db, u: StoredUser, caller: StoredUser | undefined): S['UserDetail'] => ({
  ...listed(db, u), grants: u.grants, denials: u.denials, limit_overrides: u.limit_overrides, on_shared_devices_override: u.shared_override,
  permissions: permissionsOf(db, u), limits: limitsOf(db, u), sessions: sessionsOf(db, u, caller),
  history: u.id === 'u1' || u.id === 'u2' ? Object.entries(progress).map(([ref_id, p], i) => ({ ref_id, kind: ref_id.split(':')[1] ?? null, position_ms: p.pos, duration_ms: p.dur, completed: false, updated_at: ago(60 * (i + 1) * 7) })) : [],
})
const roleOut = (db: Db, r: StoredRole): S['Role'] => ({ ...r, editable: r.id !== 'owner', user_count: db.users.filter((u) => u.role_id === r.id).length })
const inviteOut = (db: Db, { token: _token, revoked, ...i }: Db['invites'][number]): S['Invite'] => ({
  ...i, display_name: i.display_name ?? null, user_id: i.user_id ?? null, role_name: db.roles.find((r) => r.id === i.role_id)?.name ?? null,
  state: revoked ? 'revoked' : i.uses >= i.max_uses ? 'used' : Date.parse(i.expires_at) <= Date.now() ? 'expired' : 'open',
})
function audit(db: Db, actor: StoredUser | undefined, action: string, target: StoredUser | null, more: Record<string, unknown> = {}) {
  db.audit.unshift({ id: ++db.seq, at: new Date().toISOString(), action, actor_user_id: actor?.id ?? null, actor_name: actor?.display_name ?? null, target_user_id: target?.id ?? null, target_name: target?.display_name ?? null, detail: more })
}
/** Their access changed: the token they hold is out of date until refreshed. */
const outdate = (db: Db, ...changed: StoredUser[]) => { for (const u of changed) if (u.profile_id && !db.stale.includes(u.profile_id)) db.stale.push(u.profile_id) }
const accepted = () => { try { return new Set(JSON.parse(sessionStorage.getItem('mock.accepted') ?? '[]') as string[]) } catch { return new Set<string>() } }
const tokensFor = (profile: Profile) =>
  ({ access_token: `mock-access-${profile.id}`, refresh_token: `mock-refresh-${profile.id}`, profile: { ...profile, disclaimer_accepted_version: accepted().has(profile.id) ? '1.0' : null } })
const profileOf = (db: Db, id: string | null | undefined) => {
  const base = profiles.find((p) => p.id === id) ?? db.profiles.find((p) => p.id === id)
  const user = db.users.find((u) => u.profile_id === id)
  return base && user ? { ...base, name: user.display_name, avatar: user.avatar ?? null, has_pin: !!user.pin } : base
}

/** A profile the three fixtures do not list: a user approved, invited or added while the mock ran. */
export const mockProfile = (id: string): Profile | undefined => profileOf(load(), id)
/** What GET /profiles lists: the fixtures, plus the caller's own when they are someone else. */
export const ownProfile = (request: Request): Profile[] => {
  const db = load()
  const mine = callerOf(db, request)?.profile_id
  return mine && !profiles.some((p) => p.id === mine) ? [profileOf(db, mine)!] : []
}
/** The legacy profile PIN and the user's PIN are one secret. */
export function setProfilePin(profileId: string, pin: string | null) {
  const db = load()
  const user = db.users.find((u) => u.profile_id === profileId)
  if (user) { user.pin = pin; save(db) }
}
/** include_adult is honoured only for a holder of watch.adult who unlocked this session. */
export function adultUnlocked(request: Request): boolean {
  const db = load()
  const user = callerOf(db, request)
  return !!user && permissionsOf(db, user).includes('watch.adult') && (db.adult[user.id] ?? 0) > Date.now()
}

/** Does the caller hold this permission? For the other fakes (rooms, recordings), which have no users of their own. */
export function holds(request: Request, permission: Permission): boolean {
  const db = load()
  const me = callerOf(db, request)
  return !!me && permissionsOf(db, me).includes(permission)
}
/** The 403 a caller without `needs` gets; null when they hold it. */
export const refusedWithout = (request: Request, needs: Permission): Response | null =>
  holds(request, needs) ? null : error(403, 'forbidden', `Missing permission: ${needs}`, { missing: [needs] })
/** A server from before users existed has none of the calls that came after. */
export const legacyServer = (): boolean => mode() === 'legacy'

type Resolver = (ctx: { db: Db; me: StoredUser; request: Request; params: Record<string, string | readonly string[] | undefined>; body: Record<string, unknown> }) => Response | Promise<Response>
/** A signed-in caller holding `needs` (when given); the handler works on one loaded copy and saves it. */
const as = (needs: Permission | null, run: Resolver) => async ({ request, params }: { request: Request; params: Record<string, string | readonly string[] | undefined> }) => {
  if (mode() === 'legacy') return gone()
  const db = load()
  const me = callerOf(db, request)
  if (!me) return error(401, 'unauthorized', 'Sign in first')
  if (needs && !permissionsOf(db, me).includes(needs)) return error(403, 'forbidden', `Missing permission: ${needs}`, { missing: [needs] })
  let body: Record<string, unknown> = {}
  try { body = (await request.clone().json()) as Record<string, unknown> } catch { /* no body */ }
  const response = await run({ db, me, request, params, body })
  if (response.ok) save(db)
  return response
}
const target = (db: Db, params: { id?: string | readonly string[] }) => db.users.find((u) => u.id === params.id)
const cleanLimits = (raw: unknown): Partial<Limits> | null => {
  const l = (raw ?? {}) as Record<string, unknown>
  const okStreams = l.max_streams === undefined || l.max_streams === null || (Number.isInteger(l.max_streams) && (l.max_streams as number) >= 1 && (l.max_streams as number) <= 100)
  const okMovie = l.rating_ceiling_movie === undefined || l.rating_ceiling_movie === null || MOVIE.includes(l.rating_ceiling_movie as string)
  const okTv = l.rating_ceiling_tv === undefined || l.rating_ceiling_tv === null || TV.includes(l.rating_ceiling_tv as string)
  return okStreams && okMovie && okTv ? (l as Partial<Limits>) : null
}
const validPin = (pin: unknown): pin is string => typeof pin === 'string' && /^\d{4,8}$/.test(pin)
const wider = (db: Db, a: StoredUser, b: StoredUser) => { const held = permissionsOf(db, b); return permissionsOf(db, a).some((p) => !held.includes(p)) }
const switchable = (db: Db) => db.users.filter((u) => u.status === 'active' && sharedOf(db, u))
const LAST_OWNER = () => error(409, 'last_owner', 'The last active owner cannot be demoted, suspended or removed')
const newToken = () => Array.from(crypto.getRandomValues(new Uint8Array(43)), (b) => 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789-_'[b % 58]).join('')

function pendingMe(db: Db, u: StoredUser, tokens: S['AuthTokens'] | null): S['Me'] {
  return { status: u.status, user: out(db, u), role: null, permissions: [], limits: OPEN, shared_device: false, adult_unlocked: false, tokens }
}
function addGoogleUser(db: Db, name: string, role: string | null, kind: 'google' | 'plex' | 'password' = 'google', more: Partial<StoredUser> = {}): StoredUser {
  const n = ++db.seq
  const user = person(`u${n}`, n, name, kind, role, { ...more, status: role ? 'active' : 'pending', created_at: ago(0), approved_at: role ? ago(0) : null, last_seen_at: role ? ago(0) : null })
  db.users.push(user)
  db.profiles.push(profileFor(user))
  return user
}

/** The sign-in ways a spec switched on ("google,plex,password,remote"), or null for a server from before them. */
const signinWays = (): string[] | null => {
  const raw = sessionStorage.getItem('mock.signin') ?? localStorage.getItem('mock.signin')
  return raw === null ? null : raw.split(',').map((w) => w.trim()).filter(Boolean)
}
const USERNAME = /^[a-z0-9._-]{3,32}$/
const COMMON = ['password123', '1234567890', 'qwertyuiop']
/** The server's rules for a new username and password; null when they pass. */
function refusal(db: Db, username: string, password: string, mine?: StoredUser): Response | null {
  if (!USERNAME.test(username)) return error(400, 'invalid_username', 'A username is 3 to 32 letters, digits, dots, dashes or underscores')
  if (password.length < 10) return error(400, 'weak_password', 'A password needs at least 10 characters')
  if (password.toLowerCase().includes(username)) return error(400, 'weak_password', 'A password cannot contain the username')
  if (COMMON.includes(password.toLowerCase())) return error(400, 'weak_password', 'That password is one of the most common; choose another')
  if (db.users.some((u) => u.username === username && u !== mine)) return error(409, 'username_taken', 'That username is taken')
  return null
}
const usable = (db: Db, token: string) => db.invites.find((i) => i.token === token && inviteOut(db, i).state === 'open')
// A fresh sign-in is a new session: one signed out earlier (a reset, an unlink) does not hold it back.
const signedIn = (db: Db, user: StoredUser) => { user.last_seen_at = ago(0); db.revoked = (db.revoked ?? []).filter((id) => id !== user.profile_id); save(db); return HttpResponse.json({ status: 'active', ...tokensFor(profileOf(db, user.profile_id)!) }) }

/** Plex sign-in, username and password, linking, and the page behind an invite: the server's answers, without a Plex to talk to. */
function signInHandlers() {
  return [
    // Plex's own page is elsewhere; the mock sends the browser straight back, as Plex does when the person has signed in.
    http.post(`${B}/auth/plex/start`, async ({ request }) => {
      if (!signinWays()?.includes('plex')) return error(403, 'plex_disabled', 'Plex sign-in is not enabled')
      const body = (await request.json()) as { return_to?: string; invite?: string; link?: boolean }
      const db = load()
      if (body.link && !callerOf(db, request)) return error(401, 'unauthorized', 'Sign in first')
      const to = /^\/(?:pair(?:\?code=[A-Z0-9]{6})?|settings)?$/.test(body.return_to ?? '/') ? body.return_to ?? '/' : '/'
      const state = `${body.link ? 'link' : 'signin'}.${newToken().slice(0, 30)}`
      sessionStorage.setItem('mock.plex.state', JSON.stringify({ state, invite: body.invite ?? '', link: !!body.link }))
      return HttpResponse.json({ auth_url: `${window.location.origin}${to}${to.includes('?') ? '&' : '?'}plex=${state}` })
    }),
    http.post(`${B}/auth/plex/complete`, async ({ request }) => {
      const body = (await request.json()) as { state?: string }
      const started = JSON.parse(sessionStorage.getItem('mock.plex.state') ?? 'null') as { state: string; invite: string; link: boolean } | null
      sessionStorage.removeItem('mock.plex.state')
      // Started by another browser, used already, or made up: one answer.
      if (!started || started.state !== body.state) return error(401, 'sign_in_expired', 'Sign-in expired or invalid; please start again')
      const db = load()
      // Which Plex account "signed in": a spec says so as "<account id>:<username>"; the default is someone new.
      const [account, handle] = (sessionStorage.getItem('mock.plex') ?? '501:plexpal').split(':') as [string, string]
      const holder = db.users.find((u) => u.plex_account === account)
      if (started.link) {
        const me = callerOf(db, request)
        if (!me) return error(401, 'unauthorized', 'Sign in to link a Plex account')
        if (holder && holder !== me) return error(409, 'identity_in_use', 'That account already signs in as someone else here')
        if (me.plex_account && me.plex_account !== account) return error(409, 'already_linked', 'Unlink the one that is linked first')
        Object.assign(me, { plex_account: account, plex_username: handle })
        audit(db, me, 'user.signin_link', me, { method: 'plex' })
        save(db)
        return HttpResponse.json({ status: 'linked' })
      }
      if (holder?.status === 'suspended') return error(403, 'user_suspended', 'This account is suspended')
      const invite = started.invite ? usable(db, started.invite) : undefined
      if (!holder && !invite && signinWays()?.includes('invite_only')) return error(403, 'invite_required', 'An invitation is required to sign in')
      const user = holder ?? addGoogleUser(db, invite?.display_name ?? 'Plex Pal', null, 'plex', { email: null, plex_account: account, plex_username: handle })
      if (user.status === 'pending' && invite) {
        invite.uses += 1
        Object.assign(user, { status: 'active', role_id: invite.role_id, approved_at: ago(0) })
        audit(db, user, 'user.invite_accept', user, { invite_id: invite.id, role_id: invite.role_id })
      } else if (!holder) audit(db, user, 'user.signup', user, {})
      if (user.status === 'pending') { save(db); return HttpResponse.json({ status: 'pending', pending_token: `mock-pending-${user.id}`, user: { display_name: user.display_name, email: user.email } } satisfies S['PendingApproval']) }
      return signedIn(db, user)
    }),
    http.post(`${B}/auth/password`, async ({ request }) => {
      if (!signinWays()?.includes('password')) return error(403, 'password_disabled', 'Username and password sign-in is not enabled')
      const body = (await request.json()) as { username?: string; password?: string }
      const name = (body.username ?? '').trim().toLowerCase()
      const db = load()
      const key = `pw:${name}`
      const fails = db.fails[key] ?? { count: 0, until: 0 }
      if (fails.until > Date.now()) return error(429, 'rate_limited', 'Too many attempts; try again later', {}, { 'Retry-After': String(Math.max(1, Math.ceil((fails.until - Date.now()) / 1000))) })
      const user = db.users.find((u) => !!u.username && u.username === name && !!u.password && u.password === body.password)
      if (!user) {
        // Five wrong attempts for a name lock it for a minute, whether or not the name exists.
        fails.count += 1
        if (fails.count >= 5) { fails.count = 0; fails.until = Date.now() + 60_000 }
        db.fails[key] = fails
        save(db)
        return error(401, 'invalid_credentials', 'Wrong username or password')
      }
      delete db.fails[key]
      if (user.status !== 'active') return error(401, 'user_suspended', 'This account is suspended')
      const answer = signedIn(db, user)
      return HttpResponse.json(await answer.json(), { headers: { 'Cache-Control': 'no-store' } })
    }),
    http.put(`${B}/auth/password`, as(null, ({ db, me, body }) => {
      if (db.shared) return error(403, 'shared_device', 'Sign-in methods cannot be changed on a shared device')
      const had = !!(me.username && me.password)
      if (had && body.current_password !== me.password) return error(403, 'wrong_password', 'That is not your current password')
      const name = me.username ?? String(body.username ?? '').trim().toLowerCase()
      const refused = refusal(db, name, String(body.new_password ?? ''), me)
      if (refused) return refused
      Object.assign(me, { username: name, password: String(body.new_password) })
      audit(db, me, had ? 'user.password_change' : 'user.password_set', me, {})
      return none()
    })),
    http.delete(`${B}/auth/methods/:method`, as(null, ({ db, me, params }) => unlink(db, me, me, String(params.method)))),
    http.post(`${B}/auth/google/link`, as(null, ({ db, me }) => {
      if (db.shared) return error(403, 'shared_device', 'Sign-in methods cannot be changed on a shared device')
      // Google's own page is elsewhere too: the identity is attached and the browser sent back, as the callback does.
      Object.assign(me, { google_linked: true, email: me.email ?? `${me.display_name.split(' ')[0]!.toLowerCase()}@example.com` })
      audit(db, me, 'user.signin_link', me, { method: 'google' })
      return HttpResponse.json({ auth_url: `${window.location.origin}/settings?linked=google` })
    })),
    http.post(`${B}/auth/invite/lookup`, async ({ request }) => {
      if (!signinWays()) return gone()
      const { token } = (await request.json()) as { token?: string }
      const db = load()
      const invite = usable(db, token ?? '')
      const nothing = { valid: false, display_name: null, username: null, setup: false }
      if (!invite) return HttpResponse.json(nothing)
      const user = invite.user_id ? db.users.find((u) => u.id === invite.user_id) : undefined
      if (invite.user_id) return HttpResponse.json(user ? { valid: true, display_name: user.display_name, username: user.username ?? null, setup: true } : nothing)
      return HttpResponse.json({ valid: true, display_name: invite.display_name ?? null, username: null, setup: false })
    }),
    http.post(`${B}/auth/invite/accept`, async ({ request }) => {
      if (!signinWays()?.includes('password')) return error(403, 'password_disabled', 'Username and password sign-in is not enabled')
      const body = (await request.json()) as { token?: string; username?: string; password?: string }
      const db = load()
      const invite = usable(db, body.token ?? '')
      if (!invite) return error(403, 'invite_invalid', 'This link can no longer be used')
      const target = invite.user_id ? db.users.find((u) => u.id === invite.user_id) : undefined
      if (invite.user_id && !target) return error(403, 'invite_invalid', 'This link can no longer be used')
      const name = target?.username ?? (body.username ?? '').trim().toLowerCase()
      const refused = refusal(db, name, body.password ?? '', target)
      if (refused) return refused
      invite.uses += 1
      const user = target ?? addGoogleUser(db, invite.display_name ?? name, invite.role_id, 'password', { email: null })
      Object.assign(user, { username: name, password: body.password, status: 'active' })
      audit(db, user, target ? 'user.password_set' : 'user.invite_accept', user, target ? { via: 'setup_link' } : { invite_id: invite.id, role_id: invite.role_id, method: 'password' })
      const answer = signedIn(db, user)
      const { status: _status, ...tokens } = (await answer.json()) as Record<string, unknown>
      return HttpResponse.json(tokens)
    }),
    http.delete(`${B}/users/:id/methods/:method`, as('users.manage', ({ db, me, params }) => {
      const u = target(db, params)
      return u ? unlink(db, me, u, String(params.method)) : gone()
    })),
    http.post(`${B}/users/:id/password-reset`, as('users.manage', ({ db, me, params }) => {
      const u = target(db, params)
      if (!u) return gone()
      if (u.status === 'pending' || !u.role_id) return error(409, 'not_active', 'Approve this user before giving them a password')
      u.password = null
      for (const i of db.invites) if (i.user_id === u.id) i.revoked = true
      db.sessions = db.sessions.filter((s) => s.user_id !== u.id)
      if (u.profile_id && u.id !== me.id) db.revoked = [...(db.revoked ?? []), u.profile_id]
      const invite = { id: `inv-${++db.seq}`, token: newToken(), role_id: u.role_id, role_name: null, created_by_user_id: me.id, created_by_name: me.display_name, created_at: ago(0), expires_at: new Date(Date.now() + 72 * 3600_000).toISOString(), max_uses: 1, uses: 0, revoked: false, display_name: u.display_name, user_id: u.id }
      db.invites.unshift(invite)
      audit(db, me, 'user.password_reset', u, { invite_id: invite.id })
      return HttpResponse.json({ url: `/invite/${invite.token}`, expires_at: invite.expires_at } satisfies S['SetupLink'])
    })),
  ]
}
/** Take one way of signing in away from `u`. Never the last; taken by someone else it signs them out. */
function unlink(db: Db, me: StoredUser, u: StoredUser, method: string): Response {
  if (!['google', 'plex', 'password'].includes(method)) return error(422, 'validation_error', 'method: unknown')
  if (u === me && db.shared) return error(403, 'shared_device', 'Sign-in methods cannot be changed on a shared device')
  const held = methodsOf(u)
  if (!held.includes(method as Method)) return error(404, 'not_found', 'That sign-in method is not linked')
  if (held.length === 1) return error(409, 'last_method', 'The last way of signing in cannot be removed')
  if (method === 'google') u.google_linked = false
  else if (method === 'plex') Object.assign(u, { plex_account: null, plex_username: null })
  else Object.assign(u, { username: null, password: null })
  if (u !== me) { db.sessions = db.sessions.filter((s) => s.user_id !== u.id); if (u.profile_id) db.revoked = [...(db.revoked ?? []), u.profile_id] }
  audit(db, me, 'user.signin_unlink', u, { method })
  return u === me ? none() : HttpResponse.json(detail(db, u, me))
}

export const peopleHandlers = [
  // Every call first meets what the server checks on every request: is this user still allowed in, and is the token current?
  http.all(`${B}/*`, ({ request }) => {
    if (mode() === 'legacy' || new URL(request.url).pathname.endsWith('/auth/refresh')) return undefined
    const db = load()
    const me = callerOf(db, request)
    if (!me) return undefined
    if (me.status === 'suspended') return error(401, 'user_suspended', 'This account is suspended')
    if (me.profile_id && db.revoked?.includes(me.profile_id)) return error(401, 'session_revoked', 'Session revoked; sign in again')
    if (me.profile_id && db.stale.includes(me.profile_id)) return error(401, 'session_stale', 'Session is out of date; refresh it')
    return undefined
  }),
  http.post(`${B}/auth/refresh`, async ({ request }) => {
    const refresh = ((await request.clone().json()) as { refresh_token?: string }).refresh_token ?? ''
    const db = load()
    const user = db.users.find((u) => `mock-refresh-${u.profile_id}` === refresh)
    if (!user || mode() === 'legacy') return undefined
    if (user.status !== 'active') return error(401, 'user_suspended', 'This account is suspended')
    if (user.profile_id && db.revoked?.includes(user.profile_id)) return error(401, 'session_revoked', 'Session revoked; sign in again')
    db.stale = db.stale.filter((id) => id !== user.profile_id)
    save(db)
    return HttpResponse.json(tokensFor(profileOf(db, user.profile_id)!))
  }),
  // A spec turns Google sign-in on by saying how the next sign-in ends: "pending", "owner" or "invite:<token>".
  http.get(`${B}/auth/config`, () => {
    // "mock.signin" stands in for a current server: the ways in it has switched on, and "remote" for a
    // browser away from the home network (no profile picker). Without it this is a server from before them.
    const ways = signinWays()
    if (ways) return HttpResponse.json({
      google_enabled: ways.includes('google'), google_required: false, plex_enabled: ways.includes('plex'), password_enabled: ways.includes('password'),
      profiles_enabled: !ways.includes('remote'), signup_mode: ways.includes('invite_only') ? 'invite_only' : 'approval',
    })
    return sessionStorage.getItem('mock.google') ? HttpResponse.json({ google_enabled: true, google_required: true, signup_mode: 'approval' }) : undefined
  }),
  ...signInHandlers(),
  http.post(`${B}/auth/google/complete`, () => {
    const how = sessionStorage.getItem('mock.google') ?? ''
    const db = load()
    if (how === 'owner') return HttpResponse.json({ status: 'active', ...tokensFor(profileOf(db, 'p1')!) })
    const invite = how.startsWith('invite:') ? db.invites.find((i) => i.token === how.slice(7) && inviteOut(db, i).state === 'open') : undefined
    if (how !== 'pending' && !how.startsWith('invite:')) return error(401, 'unauthorized', 'Sign-in completion expired; please start again')
    const known = db.users.find((u) => u.email === 'nell@example.com')
    if (known?.status === 'suspended') return error(403, 'user_suspended', 'This account is suspended')
    const user = known ?? addGoogleUser(db, 'Nell Vance', null)
    if (user.status === 'pending' && invite) {
      invite.uses += 1
      Object.assign(user, { status: 'active', role_id: invite.role_id, approved_at: ago(0), last_seen_at: ago(0) })
      audit(db, user, 'user.invite_accept', user, { invite_id: invite.id, role_id: invite.role_id })
    } else if (!known) audit(db, user, 'user.signup', user, {})
    save(db)
    if (user.status === 'pending') return HttpResponse.json({ status: 'pending', pending_token: `mock-pending-${user.id}`, user: { display_name: user.display_name, email: user.email } } satisfies S['PendingApproval'])
    return HttpResponse.json({ status: 'active', ...tokensFor(profileOf(db, user.profile_id)!) })
  }),
  http.get(`${B}/auth/me`, ({ request }) => {
    if (mode() === 'legacy') return gone()
    const db = load()
    const token = bearer(request)
    if (token.startsWith('mock-pending-')) {
      const user = db.users.find((u) => u.id === token.slice('mock-pending-'.length))
      if (!user || user.handed || user.status === 'suspended') return error(401, 'session_revoked', 'Session revoked; sign in again')
      if (user.status === 'pending') return HttpResponse.json(pendingMe(db, user, null))
      user.handed = true
      save(db)
      return HttpResponse.json({ ...pendingMe(db, user, tokensFor(profileOf(db, user.profile_id)!)), role: { id: user.role_id!, name: roleOf(db, user)!.name, builtin: roleOf(db, user)!.builtin }, permissions: permissionsOf(db, user), limits: limitsOf(db, user) })
    }
    const me = callerOf(db, request)
    // A token the fixtures never issued: answer like a server without this call.
    if (!me) return gone()
    const role = roleOf(db, me)
    return HttpResponse.json({
      status: me.status, user: out(db, me), role: role ? { id: role.id, name: role.name, builtin: role.builtin } : null, permissions: permissionsOf(db, me), limits: limitsOf(db, me),
      shared_device: db.shared, adult_unlocked: (db.adult[me.id] ?? 0) > Date.now(), tokens: null,
    } satisfies S['Me'])
  }),

  /* ---------- devices and sessions ---------- */
  http.get(`${B}/auth/sessions`, as(null, ({ db, me, request }) => {
    const other = new URL(request.url).searchParams.get('user_id')
    if (other && other !== me.id && !permissionsOf(db, me).includes('users.manage')) return error(403, 'forbidden', 'Missing permission', { missing: ['users.manage'] })
    const user = db.users.find((u) => u.id === (other ?? me.id))
    return user ? HttpResponse.json(sessionsOf(db, user, me)) : gone()
  })),
  http.delete(`${B}/auth/sessions/:sid`, as(null, ({ db, me, params }) => {
    if (params.sid === 'local') { audit(db, me, 'session.revoke', me, { sid: 'local' }); return none() }
    const session = db.sessions.find((s) => s.sid === params.sid)
    if (!session || (session.user_id !== me.id && !permissionsOf(db, me).includes('users.manage'))) return gone()
    db.sessions = db.sessions.filter((s) => s !== session)
    audit(db, me, 'session.revoke', db.users.find((u) => u.id === session.user_id) ?? null, { sid: session.sid, label: session.label })
    return none()
  })),
  http.patch(`${B}/auth/sessions/:sid`, as(null, ({ db, me, params, body }) => {
    const session = params.sid === 'local' ? null : db.sessions.find((s) => s.sid === params.sid)
    const manages = permissionsOf(db, me).includes('users.manage')
    if (params.sid !== 'local' && (!session || (session.user_id !== me.id && !manages))) return gone()
    if (typeof body.shared_device === 'boolean') {
      if (!manages) return error(403, 'forbidden', 'Missing permission', { missing: ['users.manage'] })
      if (session) session.shared_device = body.shared_device; else db.shared = body.shared_device
      audit(db, me, 'session.shared_device', me, { sid: params.sid, shared_device: body.shared_device })
    }
    if (typeof body.label === 'string') { const label = body.label.trim().slice(0, 64) || null; if (session) session.label = label; else db.label = label }
    return HttpResponse.json(session ? { ...session, current: false } : local(db, me))
  })),
  http.get(`${B}/auth/switchable`, as(null, ({ db, me }) => {
    if (mode() === 'no-switch') return gone()
    if (!db.shared) return error(403, 'not_shared_device', 'This is not a shared household device')
    return HttpResponse.json({ users: switchable(db).map((u) => ({ id: u.id, display_name: u.display_name, avatar: u.avatar ?? null, picture: u.picture ?? null, role_name: roleOf(db, u)?.name ?? null, has_pin: !!u.pin, current: u.id === me.id })) })
  })),
  http.post(`${B}/auth/switch`, async ({ request }) => {
    if (mode() === 'legacy' || mode() === 'no-switch') return gone()
    const db = load()
    const me = callerOf(db, request)
    const body = (await request.json()) as { user_id?: string; pin?: string }
    if (!me) return error(401, 'unauthorized', 'Sign in first')
    if (!db.shared) return error(403, 'not_shared_device', 'This is not a shared household device')
    const to = switchable(db).find((u) => u.id === body.user_id)
    if (!to) return gone()
    const fails = db.fails[to.id] ?? { count: 0, until: 0 }
    // Retry-After is whole seconds, as on every 429.
    const locked = () => error(429, 'rate_limited', 'Too many wrong PINs. Try again later.', {}, { 'Retry-After': String(Math.max(1, Math.ceil((fails.until - Date.now()) / 1000))) })
    if (fails.until > Date.now()) return locked()
    if (to.pin) {
      if (!body.pin) return error(401, 'pin_required', 'Enter the PIN for this user')
      if (body.pin !== to.pin) {
        fails.count += 1
        // Five failures in fifteen minutes lock this user's PIN.
        if (fails.count >= 5) { fails.count = 0; fails.until = Date.now() + 15 * 60_000 }
        db.fails[to.id] = fails
        save(db)
        return fails.until > Date.now() ? locked() : error(401, 'pin_invalid', 'Wrong PIN')
      }
    } else if (to.id !== me.id && wider(db, to, me)) return error(409, 'pin_setup_required', 'This user needs a PIN before a shared device can switch to them')
    delete db.fails[to.id]
    to.last_seen_at = ago(0)
    save(db)
    return HttpResponse.json({ ...tokensFor(profileOf(db, to.profile_id)!), user: { ...out(db, to), current: true } } satisfies S['Switched'])
  }),
  http.post(`${B}/auth/adult-unlock`, as('watch.adult', ({ db, me, body }) => {
    if (!me.pin) return error(409, 'pin_setup_required', 'Set a PIN before opening the adult category')
    if (body.pin !== me.pin) return error(403, 'pin_invalid', 'Wrong PIN')
    db.adult[me.id] = Date.now() + 4 * 3600_000
    return HttpResponse.json({ adult_unlocked_until: new Date(db.adult[me.id]!).toISOString() })
  })),
  http.post(`${B}/auth/adult-lock`, as(null, ({ db, me }) => { delete db.adult[me.id]; return none() })),

  /* ---------- users ---------- */
  http.get(`${B}/users`, as('users.manage', ({ db, request }) => {
    const status = new URL(request.url).searchParams.get('status')
    return HttpResponse.json(db.users.filter((u) => !status || u.status === status).map((u) => listed(db, u)))
  })),
  http.post(`${B}/users`, as('users.manage', ({ db, me, body }) => {
    const name = String(body.name ?? '').trim().slice(0, 64)
    if (!name) return error(422, 'invalid_name', 'name is required')
    if (body.pin != null && !validPin(body.pin)) return error(422, 'invalid_pin', 'A PIN is 4 to 8 digits')
    const role = db.roles.find((r) => r.id === (body.role_id ?? 'family'))
    if (!role) return error(404, 'not_found', 'role not found')
    const n = ++db.seq
    const user = person(`u${n}`, n, name, 'managed', role.id, { avatar: (body.avatar as string | null) ?? null, pin: (body.pin as string | null) ?? null, created_at: ago(0), approved_at: ago(0), last_seen_at: null })
    db.users.push(user)
    db.profiles.push(profileFor(user))
    audit(db, me, 'user.create', user, { kind: 'managed', role_id: role.id })
    return HttpResponse.json(out(db, user), { status: 201 })
  })),
  http.get(`${B}/users/:id`, as('users.manage', ({ db, me, params }) => { const u = target(db, params); return u ? HttpResponse.json(detail(db, u, me)) : gone() })),
  http.patch(`${B}/users/:id`, as('users.manage', ({ db, me, params, body }) => {
    const u = target(db, params)
    if (!u) return gone()
    const access = ['role_id', 'grants', 'denials', 'limits'].some((k) => k in body)
    if (access) {
      if (u.id === me.id) return error(403, 'self_edit', 'You cannot change your own role, permissions or limits')
      if (u.status === 'pending') return error(409, 'not_active', 'Approve this user before changing their access')
      const role = 'role_id' in body ? db.roles.find((r) => r.id === body.role_id) : roleOf(db, u)
      if (!role) return error(404, 'not_found', 'role not found')
      const limits = 'limits' in body ? cleanLimits(body.limits) : u.limit_overrides
      if (!limits) return error(422, 'invalid_limits', 'unknown limit value')
      if (u.role_id === 'owner' && role.id !== 'owner' && u.status === 'active' && owners(db).length === 1) return LAST_OWNER()
      const before = u.role_id
      Object.assign(u, { role_id: role.id, grants: (body.grants as Permission[] | undefined) ?? u.grants, denials: (body.denials as Permission[] | undefined) ?? u.denials, limit_overrides: limits })
      if (role.id === 'owner') Object.assign(u, { grants: [], denials: [], limit_overrides: {} })
      if (before !== role.id) audit(db, me, 'user.role_change', u, { before, after: role.id })
      else audit(db, me, 'user.access_change', u, { after: { grants: u.grants, denials: u.denials, limits: u.limit_overrides } })
    }
    if (typeof body.name === 'string' && body.name.trim()) u.display_name = body.name.trim().slice(0, 64)
    if ('avatar' in body) u.avatar = (body.avatar as string | null) ?? null
    if ('on_shared_devices' in body) u.shared_override = body.on_shared_devices === null ? null : !!body.on_shared_devices
    // One Plex account, so one linked user: linking this one unlinks whoever had it.
    if (typeof body.plex_account_linked === 'boolean') { if (body.plex_account_linked) for (const other of db.users) other.plex_account_linked = false; u.plex_account_linked = body.plex_account_linked }
    if (!access) audit(db, me, 'user.update', u, { fields: Object.keys(body).sort() })
    outdate(db, u)
    return HttpResponse.json(detail(db, u, me))
  })),
  http.post(`${B}/users/:id/approve`, as('users.manage', ({ db, me, params, body }) => {
    const u = target(db, params)
    const role = db.roles.find((r) => r.id === body.role_id)
    if (!u || !role) return gone()
    if (u.status !== 'pending') return error(409, 'not_pending', 'This user is not waiting for approval')
    Object.assign(u, { status: 'active', role_id: role.id, approved_at: ago(0) })
    audit(db, me, 'user.approve', u, { role_id: role.id })
    return HttpResponse.json(detail(db, u, me))
  })),
  http.post(`${B}/users/:id/deny`, as('users.manage', ({ db, me, params }) => {
    const u = target(db, params)
    if (!u) return gone()
    if (u.status !== 'pending') return error(409, 'not_pending', 'This user is not waiting for approval')
    db.users = db.users.filter((x) => x !== u)
    audit(db, me, 'user.deny', null, { display_name: u.display_name, email: u.email })
    return none()
  })),
  http.post(`${B}/users/:id/suspend`, as('users.manage', ({ db, me, params }) => {
    const u = target(db, params)
    if (!u) return gone()
    if (u.status !== 'active') return error(409, 'not_active', 'Only an active user can be suspended')
    if (u.role_id === 'owner' && owners(db).length === 1) return LAST_OWNER()
    u.status = 'suspended'; u.now_playing = []
    audit(db, me, 'user.suspend', u)
    return HttpResponse.json(detail(db, u, me))
  })),
  http.post(`${B}/users/:id/reinstate`, as('users.manage', ({ db, me, params }) => {
    const u = target(db, params)
    if (!u) return gone()
    if (u.status !== 'suspended') return error(409, 'not_suspended', 'This user is not suspended')
    u.status = 'active'
    audit(db, me, 'user.reinstate', u)
    return HttpResponse.json(detail(db, u, me))
  })),
  http.delete(`${B}/users/:id`, as('users.manage', ({ db, me, params }) => {
    const u = target(db, params)
    if (!u) return gone()
    if (u.role_id === 'owner' && u.status === 'active' && owners(db).length === 1) return LAST_OWNER()
    db.users = db.users.filter((x) => x !== u)
    db.sessions = db.sessions.filter((s) => s.user_id !== u.id)
    audit(db, me, 'user.remove', null, { display_name: u.display_name })
    return none()
  })),
  http.put(`${B}/users/:id/pin`, as(null, ({ db, me, params, body }) => {
    const u = target(db, params)
    if (!u) return gone()
    if (u.id !== me.id && !permissionsOf(db, me).includes('users.manage')) return error(403, 'forbidden', 'Missing permission', { missing: ['users.manage'] })
    if (body.pin != null && !validPin(body.pin)) return error(422, 'invalid_pin', 'A PIN is 4 to 8 digits')
    u.pin = (body.pin as string | null) ?? null
    const base = profiles.find((p) => p.id === u.profile_id)
    if (base) base.has_pin = !!u.pin
    audit(db, me, u.pin ? 'user.pin_set' : 'user.pin_clear', u)
    return none()
  })),

  /* ---------- roles, invites, audit ---------- */
  http.get(`${B}/roles`, as('users.manage', ({ db }) => HttpResponse.json(db.roles.map((r) => roleOut(db, r))))),
  http.post(`${B}/roles`, as('users.manage', ({ db, me, body }) => {
    const name = String(body.name ?? '').trim().slice(0, 64)
    const limits = cleanLimits(body.limits)
    if (!name) return error(422, 'invalid_name', 'name is required')
    if (!limits) return error(422, 'invalid_limits', 'unknown limit value')
    if (db.roles.some((r) => r.name.toLowerCase() === name.toLowerCase())) return error(409, 'role_exists', 'A role with this name already exists')
    const role: StoredRole = { id: `role-${++db.seq}`, name, builtin: false, permissions: ALL.filter((p) => (body.permissions as Permission[] | undefined)?.includes(p)), limits: { ...OPEN, ...limits }, on_shared_devices: body.on_shared_devices !== false, updated_at: ago(0) }
    db.roles.push(role)
    audit(db, me, 'role.create', null, { role_id: role.id, name })
    return HttpResponse.json(roleOut(db, role), { status: 201 })
  })),
  http.patch(`${B}/roles/:id`, as('users.manage', ({ db, me, params, body }) => {
    const role = db.roles.find((r) => r.id === params.id)
    if (!role) return gone()
    if (role.id === 'owner') return error(409, 'role_fixed', 'The Owner role cannot be edited')
    const limits = cleanLimits(body.limits)
    if (!limits) return error(422, 'invalid_limits', 'unknown limit value')
    if (typeof body.name === 'string' && body.name.trim() && !role.builtin) role.name = body.name.trim().slice(0, 64)
    if (Array.isArray(body.permissions)) role.permissions = ALL.filter((p) => (body.permissions as Permission[]).includes(p))
    role.limits = { ...role.limits, ...limits }
    if (typeof body.on_shared_devices === 'boolean') role.on_shared_devices = body.on_shared_devices
    role.updated_at = ago(0)
    outdate(db, ...db.users.filter((u) => u.role_id === role.id))
    audit(db, me, 'role.update', null, { role_id: role.id })
    return HttpResponse.json(roleOut(db, role))
  })),
  http.delete(`${B}/roles/:id`, as('users.manage', ({ db, me, params }) => {
    const role = db.roles.find((r) => r.id === params.id)
    if (!role) return gone()
    if (role.builtin) return error(409, 'role_builtin', 'Built-in roles cannot be removed')
    if (db.users.some((u) => u.role_id === role.id)) return error(409, 'role_in_use', 'Move the people who hold this role to another one first')
    db.roles = db.roles.filter((r) => r !== role)
    audit(db, me, 'role.delete', null, { role_id: role.id, name: role.name })
    return none()
  })),
  http.get(`${B}/invites`, as('users.invite', ({ db, me }) => {
    const all = permissionsOf(db, me).includes('users.manage')
    return HttpResponse.json(db.invites.filter((i) => all || i.created_by_user_id === me.id).map((i) => inviteOut(db, i)))
  })),
  http.post(`${B}/invites`, as('users.invite', ({ db, me, body }) => {
    const role = db.roles.find((r) => r.id === body.role_id)
    if (!role) return error(404, 'not_found', 'role not found')
    const held = permissionsOf(db, me)
    if (role.id === 'owner' || role.permissions.some((p) => !held.includes(p))) return error(403, 'invite_role_too_wide', 'You can only invite to a role no wider than your own')
    const hours = Number(body.expires_in_hours ?? 72), uses = Number(body.max_uses ?? 1)
    if (!(hours >= 1 && hours <= 720) || !(uses >= 1 && uses <= 50)) return error(422, 'invalid_invite', 'expiry is 1 to 720 hours, uses 1 to 50')
    const named = String(body.name ?? '').trim().slice(0, 64) || null
    if (named && uses !== 1) return error(400, 'invalid_invite', 'An invite for one person is used once')
    const invite = { id: `inv-${++db.seq}`, token: newToken(), role_id: role.id, role_name: role.name, created_by_user_id: me.id, created_by_name: me.display_name, created_at: ago(0), expires_at: new Date(Date.now() + hours * 3600_000).toISOString(), max_uses: uses, uses: 0, revoked: false, display_name: named, user_id: null }
    db.invites.unshift(invite)
    audit(db, me, 'invite.create', null, { invite_id: invite.id, role_id: role.id })
    return HttpResponse.json({ ...inviteOut(db, invite), url: `/invite/${invite.token}` } satisfies S['InviteCreated'], { status: 201 })
  })),
  http.delete(`${B}/invites/:id`, as('users.invite', ({ db, me, params }) => {
    const invite = db.invites.find((i) => i.id === params.id)
    if (!invite || (invite.created_by_user_id !== me.id && !permissionsOf(db, me).includes('users.manage'))) return gone()
    invite.revoked = true
    audit(db, me, 'invite.revoke', null, { invite_id: invite.id, role_id: invite.role_id })
    return none()
  })),
  http.get(`${B}/audit`, as('users.manage', ({ db, request }) => {
    const q = new URL(request.url).searchParams
    const about = q.get('user_id')
    return HttpResponse.json(db.audit.filter((a) => !about || a.target_user_id === about || a.actor_user_id === about).slice(0, Number(q.get('limit') ?? 100)))
  })),
]
