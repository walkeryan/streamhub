import type { Profile } from '../api/types'

export type Capability = 'sources' | 'users' | 'settings.admin' | 'requests' | 'recordings' | 'sports'

/**
 * May this profile use an area of the app? Navigation and screens ask here and nowhere else, so a
 * permission system can replace this body without touching them. Today the only signal is the
 * profile's admin flag: managing areas need an unrestricted admin, watching areas are open to all.
 * This decides what is offered; the server still authorizes every request.
 */
export function can(profile: Pick<Profile, 'is_admin' | 'content_restricted'> | null | undefined, capability: Capability): boolean {
  if (!profile) return false
  switch (capability) {
    case 'requests':
    case 'sports':
      return true
    case 'sources':
    case 'users':
    case 'settings.admin':
    case 'recordings':
      return !!profile.is_admin && !profile.content_restricted
  }
}
