import { create } from 'zustand'
import { z } from 'zod'

// A sign-in that is waiting for the owner. Kept for this tab only, so a reload goes on waiting;
// the token opens GET /auth/me and nothing else.
const KEY = 'sh.pending.v1'
const INVITE_KEY = 'sh.invite.v1'
const schema = z.object({ token: z.string().min(1), name: z.string(), email: z.string().nullable(), invited: z.boolean() })
export type PendingSignIn = z.infer<typeof schema>

function read(): PendingSignIn | null {
  try {
    const parsed = schema.safeParse(JSON.parse(sessionStorage.getItem(KEY) ?? 'null'))
    return parsed.success ? parsed.data : null
  } catch { return null }
}
function write(key: string, value: string | null) {
  try { if (value === null) sessionStorage.removeItem(key); else sessionStorage.setItem(key, value) } catch { /* this tab only holds it in memory then */ }
}

export const usePending = create<{ pending: PendingSignIn | null; wait: (p: PendingSignIn) => void; stop: () => void }>((set) => ({
  pending: read(),
  wait: (pending) => { set({ pending }); write(KEY, JSON.stringify(pending)) },
  stop: () => { set({ pending: null }); write(KEY, null) },
}))

/** The sign-in about to start carries an invitation. Read once when Google hands the browser back. */
export const markInvited = () => write(INVITE_KEY, '1')
export function takeInvited(): boolean {
  try { const invited = sessionStorage.getItem(INVITE_KEY) === '1'; write(INVITE_KEY, null); return invited } catch { return false }
}
