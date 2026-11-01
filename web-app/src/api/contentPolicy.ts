import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from './client'
import type { components } from './schema'
import { useSession } from '../store/session'

export type ContentPolicy = components['schemas']['ContentPolicy']
export type ContentPolicyInput = components['schemas']['ContentPolicyInput']
export interface AuthCapabilities { google_enabled: boolean; google_required: boolean; content_policy_enabled?: boolean; owner_pin_configured?: boolean; owner_pin_setup_required?: boolean }
export function useAuthCapabilities() {
  return useQuery({ queryKey: ['auth-config'], queryFn: async ({ signal }) => {
    const r = await fetch('/api/v1/auth/config', { signal })
    if (!r.ok) throw new Error('Cannot load sign-in capabilities.')
    return await r.json() as AuthCapabilities
  } })
}
export function useContentPolicy(id: string | null, enabled: boolean) {
  const profileId = useSession((s) => s.profile?.id)
  return useQuery({ queryKey: ['content-policy', profileId, id], enabled: enabled && !!id,
    queryFn: ({ signal }) => unwrap<ContentPolicy>(api.GET('/profiles/{id}/content-policy', { params: { path: { id: id! } }, signal })),
  })
}
