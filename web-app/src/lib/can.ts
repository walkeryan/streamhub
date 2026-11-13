import type { components } from '../api/schema'
import type { Profile } from '../api/types'

type Permission = components['schemas']['Permission']
/** An area of the app, or a permission by name. */
export type Capability = 'sources' | 'users' | 'settings.admin' | 'requests' | 'recordings' | 'sports' | Permission

const AREAS: Record<Exclude<Capability, Permission>, Permission> = {
  sources: 'sources.manage',
  users: 'users.manage',
  'settings.admin': 'system.manage',
  requests: 'requests.create',
  recordings: 'recordings.view',
  // Sports is live channels and their guide.
  sports: 'watch.live',
}
// What an admin profile could do before permissions existed; everything else was open to everyone.
const ADMIN_ONLY = new Set<Permission>(['users.manage', 'sources.manage', 'system.manage', 'recordings.view', 'recordings.schedule', 'requests.approve', 'requests.auto_approve', 'users.invite', 'watch.adult'])

export interface Access {
  profile: Pick<Profile, 'is_admin' | 'content_restricted'> | null | undefined
  /** Effective permissions from /auth/me. `undefined` while it loads; `null` when the server has no such call. */
  permissions: readonly string[] | null | undefined
}

/**
 * May this user use an area of the app, or do what a permission names? Navigation and screens ask
 * here and nowhere else: an area by its name, anything narrower by the permission itself (Watch
 * together is `rooms.create`, Join a room `rooms.join`). The answer comes from the permissions
 * /auth/me reports; only against a server without /auth/me does it fall back to the profile's admin
 * flag. This decides what is offered; the server still authorizes every request.
 */
export function can(access: Access, capability: Capability): boolean {
  if (!access.profile) return false
  const permission: Permission = capability in AREAS ? AREAS[capability as keyof typeof AREAS] : (capability as Permission)
  if (access.permissions === undefined) return false
  if (access.permissions !== null) return access.permissions.includes(permission)
  // The Users screen is built on the same server calls as /auth/me: without one there is no other.
  if (capability === 'users') return false
  // Watch rooms arrived after permissions did: a server that cannot say who may use them has none to offer.
  if (permission === 'rooms.create' || permission === 'rooms.join') return false
  return ADMIN_ONLY.has(permission) ? !!access.profile.is_admin && !access.profile.content_restricted : true
}
