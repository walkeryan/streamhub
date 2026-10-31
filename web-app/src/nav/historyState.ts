import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from 'react'
import { useLocation } from 'react-router'
import { useSession } from '../store/session'

// A bounded, in-memory cache for Back navigation; never shared between profiles or browser sessions.
const entries = new Map<string, unknown>()
useSession.subscribe((session, previous) => {
  if (session.profile?.id !== previous.profile?.id) entries.clear()
})

export function useHistoryState<T>(field: string, initial: T): [T, Dispatch<SetStateAction<T>>] {
  const location = useLocation()
  const profile = useSession((s) => s.profile?.id ?? '')
  const key = `${profile}:${location.key}:${field}`
  const read = () => entries.has(key) ? entries.get(key) as T : initial
  const [entry, setEntry] = useState(() => ({ key, value: read() }))
  if (entry.key !== key) setEntry({ key, value: read() })
  useEffect(() => {
    entries.set(entry.key, entry.value)
    if (entries.size > 500) entries.delete(entries.keys().next().value!)
  }, [entry])
  const update = useCallback<Dispatch<SetStateAction<T>>>((next) => setEntry((previous) => ({ ...previous, value: typeof next === 'function' ? (next as (value: T) => T)(previous.value) : next })), [])
  return [entry.value, update]
}
