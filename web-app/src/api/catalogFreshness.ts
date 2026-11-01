import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from './client'
import type { Source } from './types'
import { useSession } from '../store/session'

const CATALOG_KEYS = new Set(['home', 'channels', 'channel-groups', 'channel-languages', 'browse', 'browse-categories', 'browse-languages', 'browse-genres', 'search', 'title', 'playback-options', 'epg', 'sports'])
function fingerprint(sources: Source[]) {
  return JSON.stringify([...sources].sort((a, b) => a.id.localeCompare(b.id)).map((source) => [
    source.id, source.enabled, source.status, source.last_sync, source.name, source.priority,
    Object.entries(source.counts ?? {}).sort(([a], [b]) => a.localeCompare(b)),
  ]))
}

/** One shared source query; no catalog-wide polling. Refresh active views only
 * when the source snapshot changes, with a faster interval during an import. */
export function useCatalogFreshness(profileId: string | undefined) {
  const qc = useQueryClient()
  const seen = useRef<{ profile: string; fingerprint: string } | null>(null)
  const sources = useQuery({
    queryKey: ['sources', profileId], enabled: !!profileId,
    queryFn: () => {
      if (useSession.getState().profile?.id !== profileId) throw new Error('Profile changed.')
      return unwrap<Source[]>(api.GET('/sources'))
    },
    staleTime: 15_000,
    refetchOnWindowFocus: true,
    refetchInterval: (query) => query.state.data?.some((source) => source.status === 'syncing') ? 5_000 : 60_000,
    refetchIntervalInBackground: false,
  })
  useEffect(() => {
    if (!profileId || !sources.data || useSession.getState().profile?.id !== profileId) return
    const next = fingerprint(sources.data)
    if (seen.current?.profile === profileId && seen.current.fingerprint === next) return
    seen.current = { profile: profileId, fingerprint: next }
    void qc.invalidateQueries({ predicate: (query) => query.queryKey[1] === profileId && CATALOG_KEYS.has(String(query.queryKey[0])) }, { cancelRefetch: false })
  }, [profileId, sources.data, qc])
}
