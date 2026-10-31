import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import { api, ok, unwrap } from './client'
import { useSession } from '../store/session'
import type {
  BrowsePage, ChannelGroup, ChannelsResult, Disclaimer, EpgData, HomeData, LanguageCount, Profile, SearchResult,
  Settings, Source, SourceCreate, Theme, Tile, TitleDetail, MediaKind,
} from './types'

const useProfileId = () => useSession((s) => s.profile?.id ?? null)
// Previous pages may be retained only within the same profile.
function useProfilePlaceholder<T>() {
  const profileId = useProfileId()
  return (data: T | undefined, query?: { queryKey: readonly unknown[] }) => query?.queryKey[1] === profileId ? data : undefined
}

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
  useQuery({ queryKey: [...qk.profiles, useProfileId()], queryFn: () => unwrap<Profile[]>(api.GET('/profiles')) })

export const useDisclaimer = () =>
  useQuery({ queryKey: qk.disclaimer, queryFn: () => unwrap<Disclaimer>(api.GET('/disclaimer')), staleTime: Infinity })

export const useHome = () => useQuery({ queryKey: [...qk.home, useProfileId()], queryFn: () => unwrap<HomeData>(api.GET('/home')), staleTime: 30_000 })

export const useSearch = (q: string, filters: { language?: string; source_id?: string } = {}) =>
  useQuery({
    queryKey: ['search', useProfileId(), q, filters],
    enabled: q.trim().length > 1,
    placeholderData: useProfilePlaceholder<SearchResult>(),
    queryFn: () => unwrap<SearchResult>(api.GET('/search', { params: { query: { q: q.trim(), ...filters } } })),
  })

export const useTitle = (id: string | undefined) =>
  useQuery({
    queryKey: ['title', useProfileId(), id],
    enabled: !!id,
    queryFn: () => unwrap<TitleDetail>(api.GET('/titles/{id}', { params: { path: { id: id! } } })),
  })

export interface BrowseFilters { source_id?: string; language?: string; q?: string; category?: string; sort?: 'added' | 'title' | 'year' | 'rating' }
export const useBrowse = (section: string, f: BrowseFilters, page = 1) =>
  useQuery({
    queryKey: ['browse', useProfileId(), section, f, page],
    placeholderData: useProfilePlaceholder<BrowsePage>(),
    queryFn: () => unwrap<BrowsePage>(api.GET('/browse/{section}', { params: { path: { section }, query: { ...f, page } } })),
  })

export const useBrowseCategories = (section: string, f: BrowseFilters) =>
  useQuery({ queryKey: ['browse-categories', useProfileId(), section, f], queryFn: () => unwrap<Array<{ name: string; count: number }>>(api.GET('/browse/{section}/categories', { params: { path: { section }, query: f } })) })
export const useBrowseLanguages = (section: string, source_id?: string) =>
  useQuery({ queryKey: ['browse-languages', useProfileId(), section, source_id], queryFn: () => unwrap<LanguageCount[]>(api.GET('/browse/{section}/languages', { params: { path: { section }, query: { source_id } } })) })

export interface ChannelFilters { group?: string; language?: string; favorites?: boolean; q?: string; source_id?: string; limit?: number; offset?: number; has_epg?: boolean; include_hidden?: boolean }
export const useChannels = (f: ChannelFilters, enabled = true) => {
  const preferred = useSession((s) => s.profile?.languages.join(',') ?? '')
  return useQuery({
    queryKey: ['channels', useProfileId(), f, preferred],
    enabled,
    placeholderData: useProfilePlaceholder<ChannelsResult & { total?: number; offset?: number; limit?: number }>(),
    queryFn: () => unwrap<ChannelsResult & { total?: number; offset?: number; limit?: number }>(api.GET('/channels', { params: { query: f } })),
  })
}

export const useChannelGroups = (filters: ChannelFilters = {}) => {
  const preferred = useSession((s) => s.profile?.languages.join(',') ?? '')
  return useQuery({ queryKey: ['channel-groups', useProfileId(), filters, preferred], queryFn: () => unwrap<ChannelGroup[]>(api.GET('/channels/groups', { params: { query: filters } })) })
}

export const useLanguages = () =>
  useQuery({ queryKey: ['channel-languages', useProfileId()], queryFn: () => unwrap<LanguageCount[]>(api.GET('/channels/languages')) })

export const useEpg = (ids: string[], from: string, to: string) =>
  useQuery({
    queryKey: ['epg', useProfileId(), ids.join(','), from, to],
    enabled: ids.length > 0,
    staleTime: 60_000,
    queryFn: () => unwrap<EpgData>(api.GET('/epg', { params: { query: { channel_ids: ids.join(','), from, to } } })),
  })

export const useFavorites = () =>
  useQuery({ queryKey: [...qk.favorites, useProfileId()], queryFn: () => unwrap<Tile[]>(api.GET('/favorites')) })

// Contract leaves the /watchlist GET response untyped; the server returns Tile[] (assumed).
export const useWatchlist = () =>
  useQuery({
    queryKey: [...qk.watchlist, useProfileId()],
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

export const useSources = () => useQuery({ queryKey: [...qk.sources, useProfileId()], queryFn: () => unwrap<Source[]>(api.GET('/sources')) })
export const useSettings = () => useQuery({ queryKey: [...qk.settings, useProfileId()], queryFn: () => unwrap<Settings>(api.GET('/settings')) })
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
    onSuccess: () => {
      for (const queryKey of [qk.profiles, ['current-profile'], qk.home, ['channels'], ['channel-groups'], ['search'], ['browse'], ['epg']]) void qc.invalidateQueries({ queryKey })
    },
  })
}
