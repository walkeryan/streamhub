import type { components } from '../api/schema'
import type { AuditEntry } from '../api/people'

type Permission = components['schemas']['Permission']
export type Limits = components['schemas']['Limits']

/** Every permission the server checks, in the order and words the Users screen shows them. */
export const PERMISSION_GROUPS: Array<{ title: string; items: Array<{ id: Permission; label: string; hint: string }> }> = [
  { title: 'Watching', items: [
    { id: 'watch.library', label: 'Library', hint: 'Movies and shows from your own library' },
    { id: 'watch.vod', label: 'On demand', hint: 'Movies and series from IPTV sources' },
    { id: 'watch.live', label: 'Live TV', hint: 'Channels, the guide and sports' },
    { id: 'watch.adult', label: 'Adult titles', hint: 'Still locked behind their own PIN' },
  ] },
  { title: 'Watching together', items: [
    { id: 'rooms.join', label: 'Join watch rooms', hint: 'Watch along when invited' },
    { id: 'rooms.create', label: 'Start watch rooms', hint: 'Invite others to watch along' },
  ] },
  { title: 'Recordings', items: [
    { id: 'recordings.view', label: 'Watch recordings', hint: 'Play what has been recorded' },
    { id: 'recordings.schedule', label: 'Schedule recordings', hint: 'Record programmes and set rules' },
  ] },
  { title: 'Requests', items: [
    { id: 'requests.create', label: 'Request titles', hint: 'Ask for movies and shows no source has' },
    { id: 'requests.auto_approve', label: 'Requests skip approval', hint: 'What they ask for is sent straight away' },
    { id: 'requests.approve', label: 'Approve requests', hint: "Decide other people's requests" },
  ] },
  { title: 'Access', items: [
    { id: 'remote.access', label: 'Away from home', hint: 'Use StreamHub outside the home network' },
    { id: 'users.invite', label: 'Invite people', hint: 'Create invite links, for roles no wider than their own' },
  ] },
  { title: 'Managing', items: [
    { id: 'users.manage', label: 'Manage users', hint: 'This screen: people, roles, devices' },
    { id: 'sources.manage', label: 'Manage sources', hint: 'Add, sync and remove sources' },
    { id: 'system.manage', label: 'Manage the server', hint: 'Settings, stream recovery, parental policy' },
  ] },
]
export const PERMISSION_COUNT = PERMISSION_GROUPS.reduce((n, g) => n + g.items.length, 0)

// The ceilings the server accepts (the contract's enums), lowest first.
export const MOVIE_RATINGS = ['G', 'PG', 'PG-13', 'R', 'NC-17'] as const
export const TV_RATINGS = ['TV-Y', 'TV-Y7', 'TV-G', 'TV-PG', 'TV-14', 'TV-MA'] as const
export const STREAM_CHOICES = [1, 2, 3, 4, 6]

export function limitsSummary(l: Limits): string {
  const parts = [l.max_streams ? `${l.max_streams} stream${l.max_streams > 1 ? 's' : ''} at once` : 'Any number of streams']
  if (l.rating_ceiling_movie || l.rating_ceiling_tv) parts.push(`up to ${[l.rating_ceiling_movie, l.rating_ceiling_tv].filter(Boolean).join(' / ')}`)
  if (!l.allow_unrated) parts.push('unrated hidden')
  return parts.join(' · ')
}

/** "5 min ago", "3 h ago", "yesterday", then the date. Null reads as never. */
export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return 'never'
  const minutes = Math.floor((now - Date.parse(iso)) / 60_000)
  if (minutes < 2) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  if (minutes < 60 * 24) return `${Math.floor(minutes / 60)} h ago`
  if (minutes < 60 * 48) return 'yesterday'
  return new Date(iso).toLocaleDateString([], { day: 'numeric', month: 'short', year: minutes > 60 * 24 * 300 ? 'numeric' : undefined })
}

export const SIGN_IN: Record<string, string> = { google: 'Google', plex: 'Plex', password: 'Password', managed: 'Managed' }
/** "Google sign-in", "Google + Plex sign-in"; someone with no sign-in of their own is "Managed". */
export function signInSummary(u: { kind: string; sign_in_methods?: string[] }): string {
  const held = u.sign_in_methods ?? []
  // A server from before linked sign-ins says only how the user came to be.
  return `${held.length ? held.map((m) => SIGN_IN[m] ?? m).join(' + ') : SIGN_IN[u.kind] ?? u.kind} sign-in`
}
export const STATUS: Record<string, string> = { active: 'Active', pending: 'Waiting for approval', suspended: 'Suspended' }
export const DEVICE_KIND: Record<string, string> = { browser: 'Browser', tv: 'TV', api: 'App' }

/** A name for a device that was never given one, from what its browser says it is. */
export function deviceName(label: string | null | undefined, kind: string, agent: string | null | undefined): string {
  if (label?.trim()) return label.trim()
  const a = agent ?? ''
  const system = /iPhone/.test(a) ? 'iPhone' : /iPad/.test(a) ? 'iPad' : /Android/.test(a) ? 'Android' : /Mac OS X|Macintosh/.test(a) ? 'Mac' : /Windows/.test(a) ? 'Windows' : /Linux/.test(a) ? 'Linux' : ''
  const what = kind === 'tv' ? 'TV' : kind === 'api' ? 'App' : 'Browser'
  return system ? `${what} on ${system}` : what
}

const said = (value: unknown) => (typeof value === 'string' && value ? value : null)
/** One line of the audit log in words. An action this build does not know is shown by its name. */
export function auditLine(e: AuditEntry, roleName: (id: string) => string): string {
  const who = e.actor_name ?? 'Someone'
  const whom = e.target_name ?? said(e.detail.display_name) ?? 'a user'
  const role = said(e.detail.role_id) ?? said(e.detail.after)
  switch (e.action) {
    case 'user.signup': return `${whom} signed in for the first time and asked to join`
    case 'user.approve': return `${who} approved ${whom}${role ? ` as ${roleName(role)}` : ''}`
    case 'user.deny': return `${who} declined ${whom}`
    case 'user.create': return `${who} added ${whom}${role ? ` as ${roleName(role)}` : ''}`
    case 'user.invite_accept': return `${whom} joined with an invite${role ? ` as ${roleName(role)}` : ''}`
    case 'user.role_change': return `${who} changed ${whom}'s role${role ? ` to ${roleName(role)}` : ''}`
    case 'user.access_change': return `${who} changed ${whom}'s permissions or limits`
    case 'user.update': return `${who} edited ${whom}`
    case 'user.suspend': return `${who} suspended ${whom}`
    case 'user.reinstate': return `${who} reinstated ${whom}`
    case 'user.remove': return `${who} removed ${whom}`
    case 'user.pin_set': return `${who} set a PIN for ${whom}`
    case 'user.pin_clear': return `${who} cleared ${whom}'s PIN`
    case 'user.signin_link': return `${whom} linked ${SIGN_IN[said(e.detail.method) ?? ''] ?? 'a'} sign-in`
    case 'user.signin_unlink': return `${who} removed ${SIGN_IN[said(e.detail.method) ?? ''] ?? 'a'} sign-in${e.target_name && e.target_name !== who ? ` from ${e.target_name}` : ''}`
    case 'user.password_set': return `${whom} chose a password`
    case 'user.password_change': return `${whom} changed their password`
    case 'user.password_reset': return `${who} reset ${whom}'s password and made a setup link`
    case 'user.plex_friend_accept': return `${whom} joined through Plex, as someone the Plex server is shared with`
    case 'auth.lockout': return `Too many wrong sign-in attempts${e.target_name ? ` for ${e.target_name}` : e.detail.scope === 'address' ? ' from one address' : ''}: locked for a while`
    case 'session.refresh_reuse': return `A device of ${whom} was signed out: an old sign-in token was used again`
    case 'role.create': return `${who} created the role ${said(e.detail.name) ?? (role ? roleName(role) : '')}`.trim()
    case 'role.update': return `${who} edited the role ${role ? roleName(role) : ''}`.trim()
    case 'role.delete': return `${who} deleted the role ${said(e.detail.name) ?? ''}`.trim()
    case 'invite.create': return `${who} created an invite${role ? ` for ${roleName(role)}` : ''}`
    case 'invite.revoke': return `${who} revoked an invite${role ? ` for ${roleName(role)}` : ''}`
    case 'session.revoke': return `${who} signed out a device${e.target_name && e.target_name !== who ? ` of ${e.target_name}` : ''}`
    case 'session.shared_device': return `${who} ${e.detail.shared_device ? 'marked a device as shared' : 'made a device personal again'}`
    default: return `${who}: ${e.action}${e.target_name ? ` (${e.target_name})` : ''}`
  }
}
