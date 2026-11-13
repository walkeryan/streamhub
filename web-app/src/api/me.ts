import { useQuery } from '@tanstack/react-query'
import { ME_KEY, absent, api, unwrap } from './client'
import type { components } from './schema'
import { can, type Access, type Capability } from '../lib/can'
import { useSession } from '../store/session'

export type Permission = components['schemas']['Permission']
export type Me = components['schemas']['Me']
export type Limits = components['schemas']['Limits']

/**
 * Who the signed-in user is and what they may do. Read again at every sign-in and switch (the key
 * carries both), when the window regains focus, and when the API layer sees `session_stale` or a
 * missing permission. `null` means the server has no /auth/me: an older server, where the profile's
 * admin flag is the only signal there is.
 */
export function useMe() {
  const profileId = useSession((s) => s.profile?.id ?? null)
  const generation = useSession((s) => s.generation)
  return useQuery<Me | null>({
    queryKey: [...ME_KEY, profileId, generation],
    enabled: !!profileId,
    staleTime: 30_000,
    refetchOnWindowFocus: 'always',
    // The same user signing in again keeps what is known about them while the answer is fetched anew.
    placeholderData: (previous, query) => (query?.queryKey[1] === profileId ? previous : undefined),
    queryFn: async ({ signal }) => {
      try { return await unwrap<Me>(api.GET('/auth/me', { signal })) } catch (e) { if (absent(e)) return null; throw e }
    },
  })
}

/** What `can` judges: the profile, and the permission set once /auth/me has answered. */
export function useAccess(): Access {
  const profile = useSession((s) => s.profile)
  const me = useMe()
  return { profile, permissions: me.data === undefined ? undefined : me.data === null ? null : me.data.permissions }
}

/** May the signed-in user do this? Takes a permission, or one of the app's named areas. */
export const useCan = (capability: Capability): boolean => can(useAccess(), capability)
