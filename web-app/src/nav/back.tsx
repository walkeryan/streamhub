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
useSession.subscribe((session, previous) => {
  if (session.profile?.id !== previous.profile?.id) memory.clear()
})

export function useGo() {
  const navigate = useNavigate()
  const loc = useLocation()
  return (to: string, opts?: { replace?: boolean }) => {
    memory.set(loc.key, getCurrentFocusKey())
    navigate(to, opts)
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
      if (b) window.setTimeout(() => { if (doesFocusableExist(b)) void setFocus(b) }, 20)
    }
  }, [open, firstKey])
}
