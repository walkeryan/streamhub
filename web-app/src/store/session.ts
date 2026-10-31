import { create } from 'zustand'
import { z } from 'zod'
import type { Profile } from '../api/types'
import { lsGet, lsSet } from '../lib/util'

const KEY = 'sh.session.v1'
const sessionSchema = z.object({
  access: z.string(),
  refresh: z.string(),
  profile: z.object({ id: z.string(), name: z.string() }).passthrough(),
})

interface SessionState {
  access: string | null
  refresh: string | null
  profile: Profile | null
  setSession: (access: string, refresh: string, profile: Profile) => void
  setProfile: (profile: Profile) => void
  clear: () => void
}

function load(): Pick<SessionState, 'access' | 'refresh' | 'profile'> {
  const raw = lsGet(KEY)
  if (raw) {
    try {
      const r = sessionSchema.safeParse(JSON.parse(raw))
      if (r.success) return { access: r.data.access, refresh: r.data.refresh, profile: r.data.profile as unknown as Profile }
    } catch {
      /* ignore */
    }
  }
  return { access: null, refresh: null, profile: null }
}

const persist = (s: Pick<SessionState, 'access' | 'refresh' | 'profile'>) =>
  lsSet(KEY, s.access && s.refresh && s.profile ? JSON.stringify({ access: s.access, refresh: s.refresh, profile: s.profile }) : null)

export const useSession = create<SessionState>((set, get) => ({
  ...load(),
  setSession: (access, refresh, profile) => {
    set({ access, refresh, profile })
    persist(get())
  },
  setProfile: (profile) => {
    set({ profile })
    persist(get())
  },
  clear: () => {
    set({ access: null, refresh: null, profile: null })
    persist(get())
  },
}))
