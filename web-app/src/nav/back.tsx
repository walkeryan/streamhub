import { useEffect, useRef } from 'react'
import { useSession } from '../store/session'
import { useLocation, useNavigate } from 'react-router'
import { doesFocusableExist, getCurrentFocusKey, setFocus } from './spatial'

/* ---------------- Back stack: Escape / Backspace / platform Back ---------------- */
type BackHandler = () => boolean | void
const stack: BackHandler[] = []

/** Register a handler for Back while mounted/active. Last registered wins. Return false to fall through. */
export function useBackHandler(handler: BackHandler, active = true): void {
  const ref = useRef(handler)
  ref.current = handler
  useEffect(() => {
    if (!active) return
    const h: BackHandler = () => ref.current()
    stack.push(h)
    return () => {
      const i = stack.lastIndexOf(h)
      if (i >= 0) stack.splice(i, 1)
    }
  }, [active])
}

export function runBack(): boolean {
  for (let i = stack.length - 1; i >= 0; i--) {
    const h = stack[i]
    if (h && h() !== false) return true
  }
  return false
}

const BACK_KEYS = new Set(['Escape', 'BrowserBack', 'GoBack', 'XF86Back'])
const BACK_CODES = new Set([27, 10009, 461, 166]) // Esc, Tizen, webOS, Android

export function useGlobalBack(): void {
  const navigate = useNavigate()
  const loc = useLocation()
  const atRoot = useRef(true)
  atRoot.current = loc.key === 'default'
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      const typing = !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)
      const isBack = BACK_KEYS.has(e.key) || BACK_CODES.has(e.keyCode) || (e.key === 'Backspace' && !typing)
      if (!isBack) return
      if (e.defaultPrevented) return
      e.preventDefault()
      if (runBack()) return
      if (!atRoot.current) navigate(-1)
      else if (location.pathname !== '/') navigate('/', { replace: true })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navigate])
}

/* ---------------- Focus memory: Back restores the element that opened the screen ---------------- */
const memory = new Map<string, string>()
// Ephemeral navigation provenance, not a credential or a return URL. Reloads,
// profile/session changes and player replacements cannot reuse an entry.
const playbackEntries = new Map<number, {profile:string;generation:number;url:string;index:number}>()
let playbackEntrySequence = 0
export function hasPlaybackEntry(state: unknown, url: string): boolean {
  const token = state && typeof state === 'object' ? (state as {playbackEntry?:unknown}).playbackEntry : undefined
  const entry = typeof token === 'number' ? playbackEntries.get(token) : undefined
  const session = useSession.getState()
  return !!entry && entry.profile === session.profile?.id && entry.generation === session.generation && entry.url === url && window.history.state?.idx === entry.index + 1
}
useSession.subscribe((session, previous) => {
  if (session.profile?.id !== previous.profile?.id) memory.clear()
  if (session.profile?.id !== previous.profile?.id || session.generation !== previous.generation) playbackEntries.clear()
})

export function useGo() {
  const navigate = useNavigate()
  const loc = useLocation()
  return (to: string, opts?: { replace?: boolean }) => {
    memory.set(loc.key, getCurrentFocusKey())
    const session = useSession.getState()
    const index = window.history.state?.idx
    if (to.startsWith('/play/') && !loc.pathname.startsWith('/play/') && !opts?.replace && session.profile && Number.isInteger(index)) {
      const token = ++playbackEntrySequence
      playbackEntries.set(token, {profile:session.profile.id,generation:session.generation,url:to,index})
      if (playbackEntries.size > 100) playbackEntries.delete(playbackEntries.keys().next().value!)
      navigate(to, {...opts,state:{playbackEntry:token}})
    } else navigate(to, opts)
  }
}

/** Call once per screen. When `ready`, restores remembered focus for this history entry, else focuses `fallback`. */
export function useRestoreFocus(ready: boolean, fallback: string): void {
  const loc = useLocation()
  const done = useRef<string | null>(null)
  useEffect(() => {
    if (!ready || done.current === loc.key) return
    const id = window.setTimeout(() => {
      done.current = loc.key
      const saved = memory.get(loc.key)
      const target = saved && doesFocusableExist(saved) ? saved : fallback
      void setFocus(target)
    }, 30)
    return () => window.clearTimeout(id)
  }, [ready, loc.key, fallback])
}

/** Remember the focused element before opening an overlay and put focus back when it closes. */
export function useOverlayFocus(open: boolean, firstKey: string): void {
  const before = useRef<string | null>(null)
  useEffect(() => {
    if (!open) return
    before.current = getCurrentFocusKey()
    const id = window.setTimeout(() => void setFocus(firstKey), 20)
    return () => {
      window.clearTimeout(id)
      const b = before.current
      // Cleanup runs after the closing render. Restore now: a delayed restore
      // can steal focus from the user's next D-pad action after dismissal.
      if (b && doesFocusableExist(b)) void setFocus(b)
    }
  }, [open, firstKey])
}
