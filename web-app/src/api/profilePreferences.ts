import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from './client'
import { useSession } from '../store/session'

// Preserve the order of rapid edits from this browser; the server merges independent namespaces.
const writes = new Map<string, Promise<unknown>>()
type Document = Record<string, unknown>
const assertProfile = (id: string) => {
  if (useSession.getState().profile?.id !== id) throw new Error('Profile changed before preferences were saved.')
}
async function save(id: string, namespace: string, value: unknown, onlyMissing = false): Promise<Document> {
  const previous = writes.get(id) ?? Promise.resolve()
  const next = previous.catch(() => undefined).then(async () => {
    assertProfile(id)
    return unwrap<Document>(api.PATCH('/profiles/{id}/prefs', {
      params: { path: { id }, query: { only_missing: onlyMissing } }, body: { [namespace]: value },
    }))
  })
  writes.set(id, next)
  try { return await next } finally { if (writes.get(id) === next) writes.delete(id) }
}

export function useProfilePreference<T>(namespace: string, defaults: T, validate: (raw: unknown) => T | null, importDefaults = false) {
  const id = useSession((s) => s.profile?.id ?? '')
  const key = `${id}:${namespace}`
  const qc = useQueryClient()
  const revision = useRef(0)
  const [local, setLocal] = useState<{ key: string; value: T; saving: boolean; error: string | null } | null>(null)
  const query = useQuery({
    queryKey: ['profile-prefs', id], enabled: !!id, staleTime: 0, refetchOnWindowFocus: true,
    queryFn: async () => { assertProfile(id); return unwrap<Document>(api.GET('/profiles/{id}/prefs', { params: { path: { id } } })) },
  })
  const imported = useRef<string | null>(null)
  useEffect(() => {
    if (!importDefaults || !query.data || !id || namespace in query.data || imported.current === key) return
    imported.current = key
    void save(id, namespace, defaults, true).then((document) => {
      if (useSession.getState().profile?.id === id) qc.setQueryData(['profile-prefs', id], document)
    }).catch(() => { imported.current = null })
  }, [defaults, id, importDefaults, key, namespace, qc, query.data])
  const stored = validate(query.data?.[namespace]) ?? defaults
  const own = local?.key === key ? local : null
  const value = own?.value ?? stored
  const latest = useRef({ key, value })
  latest.current = { key, value }
  const update = (next: T | ((current: T) => T)) => {
    if (!query.isSuccess || !id) return
    const current = latest.current.key === key ? latest.current.value : value
    const result = typeof next === 'function' ? (next as (current: T) => T)(current) : next
    latest.current = { key, value: result }
    const version = ++revision.current
    setLocal({ key, value: result, saving: true, error: null })
    void save(id, namespace, result).then((document) => {
      // Never seed the newly selected profile's cache from a previous write.
      if (useSession.getState().profile?.id !== id) return
      qc.setQueryData(['profile-prefs', id], document)
      if (revision.current === version) setLocal((current) => current?.key === key ? null : current)
    }).catch(() => {
      if (revision.current === version) setLocal((current) => current?.key === key ? { ...current, saving: false, error: 'This preference could not be saved. Try again.' } : current)
    })
  }
  const retry = () => own?.error && query.isSuccess ? update(own.value) : query.refetch()
  return { value, update, ready: query.isSuccess, saving: own?.saving ?? false, error: own?.error ?? (query.error ? 'Saved preferences could not be loaded.' : null), retry }
}
