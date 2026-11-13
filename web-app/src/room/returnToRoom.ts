import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router'

const KEY = 'sh.roomReturn.v1'
const ROOM_PATH = /^\/room\/[A-Za-z0-9]{6}$/
/** A sign-in started from a room link is good for this long; later it is just a sign-in. */
const VALID_MS = 15 * 60_000

/**
 * A room link opened signed out: signing in can leave the page (a redirect to the identity provider
 * and back to the start page), so the room path is kept for this tab and returned to once signed in.
 * Only the path is kept, never the query.
 */
export function useReturnToRoom(signedIn: boolean): void {
  const path = useLocation().pathname
  const navigate = useNavigate()
  useEffect(() => {
    try {
      if (!signedIn) {
        if (ROOM_PATH.test(path)) sessionStorage.setItem(KEY, JSON.stringify({ path, at: Date.now() }))
        return
      }
      const raw = sessionStorage.getItem(KEY)
      if (!raw) return
      sessionStorage.removeItem(KEY)
      const saved = JSON.parse(raw) as { path?: unknown; at?: unknown }
      if (typeof saved.path !== 'string' || typeof saved.at !== 'number' || !ROOM_PATH.test(saved.path) || Date.now() - saved.at > VALID_MS) return
      if (saved.path !== path) navigate(saved.path, { replace: true })
    } catch { /* storage unavailable: the link still works when opened again */ }
  }, [signedIn, path, navigate])
}
