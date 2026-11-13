import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from './client'
import type { components } from './schema'
import { useSession } from '../store/session'

export type ContentPolicy = components['schemas']['ContentPolicy']
export type ContentPolicyInput = components['schemas']['ContentPolicyInput']
export interface AuthCapabilities {
  google_enabled: boolean; google_required: boolean; content_policy_enabled?: boolean; owner_pin_configured?: boolean; owner_pin_setup_required?: boolean
  /** Whether a new Google or Plex account waits for approval or needs an invite. */
  signup_mode?: 'approval' | 'invite_only'
  /** "Continue with Plex" and username-and-password sign-in. Absent on a server from before them: off. */
  plex_enabled?: boolean; password_enabled?: boolean
  /** The profile picker is open to this browser (it is on the home network). Absent on an older server, which always offered it. */
  profiles_enabled?: boolean
}
/**
 * What the sign-in screen has to know before anyone is signed in. GET /auth/config is the server's one
 * unauthenticated answer to that; contracts/openapi.yaml does not describe it, so its shape is kept here
 * and every field beyond the two Google flags is optional. (The contract's own signup_mode is on
 * GET /settings, which needs a session.)
 */
export function useAuthCapabilities() {
  return useQuery({ queryKey: ['auth-config'], queryFn: async ({ signal }) => {
    const r = await fetch('/api/v1/auth/config', { signal })
    if (!r.ok) throw new Error('Cannot load sign-in settings. Please retry.')
    return await r.json() as AuthCapabilities
  } })
}
export function useContentPolicy(id: string | null, enabled: boolean) {
  const profileId = useSession((s) => s.profile?.id)
  return useQuery({ queryKey: ['content-policy', profileId, id], enabled: enabled && !!id,
    queryFn: ({ signal }) => unwrap<ContentPolicy>(api.GET('/profiles/{id}/content-policy', { params: { path: { id: id! } }, signal })),
  })
}
