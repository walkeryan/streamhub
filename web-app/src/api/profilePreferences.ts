import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from './client'
import { useSession } from '../store/session'

// PUT replaces the document. Serialize our writers and fetch just before merging
// so changing one screen's defaults preserves unrelated preferences.
const writes = new Map<string, Promise<unknown>>()
type Document = Record<string, unknown>
const assertProfile = (id: string) => {
  if (useSession.getState().profile?.id !== id) throw new Error('Profile changed before preferences were saved.')
}
async function save(id: string, namespace: string, value: unknown): Promise<Document> {
  const previous = writes.get(id) ?? Promise.resolve()
  const next = previous.catch(() => undefined).then(async () => {
    assertProfile(id)
    const current = await unwrap<Document>(api.GET('/profiles/{id}/prefs', { params: { path: { id } } }))
    assertProfile(id)
    return unwrap<Document>(api.PUT('/profiles/{id}/prefs', { params: { path: { id } }, body: { ...current, [namespace]: value } }))
  })
  writes.set(id, next)
  try { return await next } finally { if (writes.get(id) === next) writes.delete(id) }
}

export function useProfilePreference<T>(namespace: string, defaults: T, validate: (raw: unknown) => T | null) {
  const id = useSession((s) => s.profile?.id ?? '')
  const key = `${id}:${namespace}`
  const qc = useQueryClient()
  const revision = useRef(0)
  const [local, setLocal] = useState<{ key: string; value: T; saving: boolean; error: string | null } | null>(null)
  const query = useQuery({
    queryKey: ['profile-prefs', id], enabled: !!id, staleTime: 0,
    queryFn: async () => { assertProfile(id); return unwrap<Document>(api.GET('/profiles/{id}/prefs', { params: { path: { id } } })) },
  })
  const stored = validate(query.data?.[namespace]) ?? defaults
  const own = local?.key === key ? local : null
  const value = own?.value ?? stored
  const update = (next: T | ((current: T) => T)) => {
    if (!query.isSuccess || !id) return
    const result = typeof next === 'function' ? (next as (current: T) => T)(value) : next
    const version = ++revision.current
    setLocal({ key, value: result, saving: true, error: null })
    void save(id, namespace, result).then((document) => {
      // Never seed the newly selected profile's cache from a previous write.
      if (useSession.getState().profile?.id !== id) return
      qc.setQueryData(['profile-prefs', id], document)
      if (revision.current === version) setLocal((current) => current?.key === key ? null : current)
    }).catch(() => {
      if (revision.current === version) setLocal((current) => current?.key === key ? { ...current, saving: false, error: 'Filters could not be saved. Try changing them again.' } : current)
    })
  }
  return { value, update, ready: query.isSuccess, saving: own?.saving ?? false, error: own?.error ?? (query.error ? 'Saved filters could not be loaded.' : null), retry: query.refetch }
}
