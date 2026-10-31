import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import { api, ok, unwrap } from './client'
import type {
  BrowsePage, ChannelGroup, ChannelsResult, Disclaimer, EpgData, HomeData, LanguageCount, Profile, SearchResult,
  Settings, Source, SourceCreate, Theme, Tile, TitleDetail, MediaKind,
} from './types'

export const qk = {
  profiles: ['profiles'] as const,
  disclaimer: ['disclaimer'] as const,
  home: ['home'] as const,
  sources: ['sources'] as const,
  favorites: ['favorites'] as const,
  watchlist: ['watchlist'] as const,
  settings: ['settings'] as const,
}

export const useProfiles = () =>
  useQuery({ queryKey: qk.profiles, queryFn: () => unwrap<Profile[]>(api.GET('/profiles')) })

export const useDisclaimer = () =>
  useQuery({ queryKey: qk.disclaimer, queryFn: () => unwrap<Disclaimer>(api.GET('/disclaimer')), staleTime: Infinity })

export const useHome = () => useQuery({ queryKey: qk.home, queryFn: () => unwrap<HomeData>(api.GET('/home')), staleTime: 30_000 })

export const useSearch = (q: string) =>
  useQuery({
    queryKey: ['search', q],
    enabled: q.trim().length > 1,
    placeholderData: keepPreviousData,
    queryFn: () => unwrap<SearchResult>(api.GET('/search', { params: { query: { q: q.trim() } } })),
  })

export const useTitle = (id: string | undefined) =>
  useQuery({
    queryKey: ['title', id],
    enabled: !!id,
    queryFn: () => unwrap<TitleDetail>(api.GET('/titles/{id}', { params: { path: { id: id! } } })),
  })

export interface BrowseFilters { source_id?: string; sort?: 'added' | 'title' | 'year' | 'rating' }
export const useBrowse = (section: string, f: BrowseFilters) =>
  useInfiniteQuery({
    queryKey: ['browse', section, f],
    initialPageParam: 1,
    queryFn: ({ pageParam }) =>
      unwrap<BrowsePage>(api.GET('/browse/{section}', { params: { path: { section }, query: { ...f, page: pageParam } } })),
    getNextPageParam: (last, all) => {
      const loaded = all.reduce((n, p) => n + (p.tiles?.length ?? 0), 0)
      return last.total !== undefined && loaded < last.total && (last.tiles?.length ?? 0) > 0 ? all.length + 1 : undefined
    },
  })

export interface ChannelFilters { group?: string; language?: string; favorites?: boolean; q?: string; source_id?: string }
export const useChannels = (f: ChannelFilters) =>
  useQuery({
    queryKey: ['channels', f],
    placeholderData: keepPreviousData,
    queryFn: () => unwrap<ChannelsResult>(api.GET('/channels', { params: { query: f } })),
  })

export const useChannelGroups = () =>
  useQuery({ queryKey: ['channel-groups'], queryFn: () => unwrap<ChannelGroup[]>(api.GET('/channels/groups')) })

export const useLanguages = () =>
  useQuery({ queryKey: ['channel-languages'], queryFn: () => unwrap<LanguageCount[]>(api.GET('/channels/languages')) })

export const useEpg = (ids: string[], from: string, to: string) =>
  useQuery({
    queryKey: ['epg', ids.join(','), from, to],
    enabled: ids.length > 0,
    staleTime: 60_000,
    queryFn: () => unwrap<EpgData>(api.GET('/epg', { params: { query: { channel_ids: ids.join(','), from, to } } })),
  })

export const useFavorites = () =>
  useQuery({ queryKey: qk.favorites, queryFn: () => unwrap<Tile[]>(api.GET('/favorites')) })

// Contract leaves the /watchlist GET response untyped; the server returns Tile[] (assumed).
export const useWatchlist = () =>
  useQuery({
    queryKey: qk.watchlist,
    queryFn: async () => {
      const r = await fetch('/api/v1/watchlist', { headers: authHeader() })
      if (!r.ok) throw new Error('watchlist')
      return (await r.json()) as Tile[]
    },
  })

function authHeader(): Record<string, string> {
  const raw = localStorage.getItem('sh.session.v1')
  try {
    const t = raw ? (JSON.parse(raw) as { access?: string }).access : undefined
    return t ? { authorization: `Bearer ${t}` } : {}
  } catch {
    return {}
  }
}

export const useSources = () => useQuery({ queryKey: qk.sources, queryFn: () => unwrap<Source[]>(api.GET('/sources')) })
export const useSettings = () => useQuery({ queryKey: qk.settings, queryFn: () => unwrap<Settings>(api.GET('/settings')) })
export const useThemes = () => useQuery({ queryKey: ['themes'], queryFn: () => unwrap<Theme[]>(api.GET('/themes')) })

const registrySchema = z.array(
  z.object({
    key: z.string().optional(),
    service_key: z.string().optional(),
    name: z.string().optional(),
    web_url: z.string().optional(),
    supports_title_link: z.boolean().optional(),
  }).passthrough(),
)
export interface ServiceInfo { key: string; name?: string; web_url?: string }
export const useServiceRegistry = () =>
  useQuery({
    queryKey: ['service-registry'],
    queryFn: async (): Promise<ServiceInfo[]> => {
      const { data } = await api.GET('/services/registry')
      const parsed = registrySchema.safeParse(data)
      if (!parsed.success) return []
      return parsed.data.flatMap((s) => {
        const key = s.key ?? s.service_key
        return key ? [{ key, name: s.name, web_url: s.web_url }] : []
      })
    },
    staleTime: 10 * 60_000,
  })

/* ---------- mutations ---------- */
export function useToggleFavorite() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (v: { kind: MediaKind; id: string; on: boolean }) => {
      if (v.on) await ok(api.PUT('/favorites', { body: { kind: v.kind, id: v.id } }))
      else await ok(api.DELETE('/favorites', { params: { query: { kind: v.kind, id: v.id } } }))
    },
    onSuccess: () => {
      for (const k of [qk.favorites, qk.home, ['channels'], ['title']]) void qc.invalidateQueries({ queryKey: k })
    },
  })
}

export function useToggleWatchlist() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: async (v: { id: string; on: boolean }) => {
      if (v.on) await ok(api.PUT('/watchlist', { body: { id: v.id } }))
      else await ok(api.DELETE('/watchlist', { params: { query: { id: v.id } } }))
    },
    onSuccess: () => {
      for (const k of [qk.watchlist, qk.home, ['title']]) void qc.invalidateQueries({ queryKey: k })
    },
  })
}

export function useCreateSource() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: SourceCreate) => unwrap<Source>(api.POST('/sources', { body })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.sources }),
  })
}

export function useDeleteSource() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => ok(api.DELETE('/sources/{id}', { params: { path: { id } } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.sources })
      void qc.invalidateQueries({ queryKey: qk.home })
    },
  })
}

export function usePatchProfile() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { id: string; patch: Partial<Profile> }) =>
      ok(api.PATCH('/profiles/{id}', { params: { path: { id: v.id } }, body: v.patch as Profile })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.profiles }),
  })
}
