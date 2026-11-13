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
  /** Changes on login/logout, not ordinary token rotation. Never persisted. */
  generation: number
  refreshSession: (access: string, refresh: string, profile: Profile, expectedRefresh: string, expectedGeneration: number) => boolean
  access: string | null
  refresh: string | null
  profile: Profile | null
  setSession: (access: string, refresh: string, profile: Profile) => void
  setProfile: (profile: Profile) => void
  clear: () => void
  /** Why the server ended the last session, for the sign-in screen. Gone at the next sign-in. Never persisted. */
  notice: string | null
  /** Sign out because the server said so, keeping the explanation. */
  end: (notice: string) => void
  /** The server answered 403 remote_access_denied: this user is served on the home network only. Never persisted. */
  away: boolean
  setAway: (away: boolean) => void
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
  generation: 0,
  notice: null,
  away: false,
  setAway: (away) => { if (get().away !== away) set({ away }) },
  refreshSession: (access, refresh, profile, expectedRefresh, expectedGeneration) => {
    if (get().generation !== expectedGeneration || get().refresh !== expectedRefresh || get().profile?.id !== profile.id) return false
    set({ access, refresh, profile }); persist(get()); return true
  },
  setSession: (access, refresh, profile) => {
    set({ access, refresh, profile, generation: get().generation + 1, notice: null, away: false })
    persist(get())
  },
  setProfile: (profile) => {
    set({ profile })
    persist(get())
  },
  clear: () => {
    set({ access: null, refresh: null, profile: null, generation: get().generation + 1, away: false })
    persist(get())
  },
  end: (notice) => {
    get().clear()
    set({ notice })
  },
}))
