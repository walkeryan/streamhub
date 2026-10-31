import { useEffect, useState } from 'react'

export const cn = (...a: Array<string | false | null | undefined>) => a.filter(Boolean).join(' ')

export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(id)
  }, [intervalMs])
  return now
}

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const id = window.setTimeout(() => setV(value), ms)
    return () => window.clearTimeout(id)
  }, [value, ms])
  return v
}

export const reducedMotion = (): boolean =>
  typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

export function fmtTime(iso: string | number | Date): string {
  return new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

export function fmtDuration(totalSec: number): string {
  const s = Math.max(0, Math.floor(totalSec))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  const mm = h ? String(m).padStart(2, '0') : String(m)
  return `${h ? `${h}:` : ''}${mm}:${String(sec).padStart(2, '0')}`
}

export function progressOf(start: string, stop: string, now: number): number {
  const a = Date.parse(start)
  const b = Date.parse(stop)
  if (!(b > a)) return 0
  return Math.min(100, Math.max(0, ((now - a) / (b - a)) * 100))
}

export function hueOf(text: string): number {
  let h = 0
  for (let i = 0; i < text.length; i++) h = (h * 31 + text.charCodeAt(i)) % 360
  return h
}

export function safeJson<T>(raw: string | null, fallback: T): T {
  if (!raw) return fallback
  try {
    return JSON.parse(raw) as T
  } catch {
    return fallback
  }
}

export function lsGet(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}
export function lsSet(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    /* storage unavailable */
  }
}

export const LANG_NAMES: Record<string, string> = {
  eng: 'English', spa: 'Spanish', fra: 'French', deu: 'German', ita: 'Italian', por: 'Portuguese',
  hin: 'Hindi', ara: 'Arabic', rus: 'Russian', zho: 'Chinese', jpn: 'Japanese', kor: 'Korean', tur: 'Turkish', und: 'Unknown',
}
