import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { z } from 'zod'
import { api, ok, unwrap } from './client'
import { useSession } from '../store/session'
import type { Recording, RecordingList, RecordingRule, RecordingRuleList, Room, RoomJoin, RoomJoined } from './types'
import type {
  PlaybackOptions, BrowsePage, ChannelGroup, ChannelsResult, Disclaimer, EpgData, HomeData, LanguageCount, Profile, SearchResult,
  Settings, ShelfList, Source, SourceCreate, Theme, Tile, TitleDetail, MediaKind, MediaRequest, Notice, RequestList, RequestManagerSettings,
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
    queryKey: ['search', useProfileId(), q.trim().toLowerCase().replace(/\s+/g, ' '), filters, useSession((s) => s.profile?.languages.join(','))],
    enabled: q.trim().length > 1,
    staleTime: 30_000,
    queryFn: ({ signal }) => unwrap<SearchResult>(api.GET('/search', { signal, params: { query: { q: q.trim(), ...filters } } })),
  })

export const useTitle = (id: string | undefined) =>
  useQuery({
    queryKey: ['title', useProfileId(), id],
    enabled: !!id,
    queryFn: () => unwrap<TitleDetail>(api.GET('/titles/{id}', { params: { path: { id: id! } } })),
  })

export interface BrowseFilters { source_id?: string; language?: string; q?: string; category?: string; genre?: string; sort?: 'added' | 'title' | 'year' | 'rating' }
export const useBrowse = (section: string, f: BrowseFilters, page = 1, enabled = true) =>
  useQuery({
    queryKey: ['browse', useProfileId(), section, f, page],
    enabled,
    placeholderData: useProfilePlaceholder<BrowsePage>(),
    queryFn: () => unwrap<BrowsePage>(api.GET('/browse/{section}', { params: { path: { section }, query: { ...f, page } } })),
  })

/** Every shelf of a section with its first titles: what Movies and Shows open on. */
export const useBrowseShelves = (section: string, f: { language?: string; limit_rows?: number; limit_items?: number }, enabled = true) =>
  useQuery({
    queryKey: ['browse-shelves', useProfileId(), section, f],
    enabled,
    queryFn: () => unwrap<ShelfList>(api.GET('/browse/{section}/shelves', { params: { path: { section }, query: f } })),
  })

export const useBrowseCategories = (section: string, f: BrowseFilters) =>
  useQuery({ queryKey: ['browse-categories', useProfileId(), section, f], queryFn: () => unwrap<Array<{ name: string; count: number }>>(api.GET('/browse/{section}/categories', { params: { path: { section }, query: f } })) })
export const useBrowseLanguages = (section: string, source_id?: string) =>
  useQuery({ queryKey: ['browse-languages', useProfileId(), section, source_id], queryFn: () => unwrap<LanguageCount[]>(api.GET('/browse/{section}/languages', { params: { path: { section }, query: { source_id } } })) })

export interface ChannelFilters { country?: string; group?: string; language?: string; favorites?: boolean; q?: string; source_id?: string; limit?: number; offset?: number; has_epg?: boolean; include_hidden?: boolean }
export const useChannels = (f: ChannelFilters, enabled = true) => {
  const preferred = useSession((s) => s.profile?.languages.join(',') ?? '')
  return useQuery({
    queryKey: ['channels', useProfileId(), f, preferred],
    enabled,
    placeholderData: useProfilePlaceholder<ChannelsResult & { total?: number; offset?: number; limit?: number }>(),
    queryFn: () => unwrap<ChannelsResult & { total?: number; offset?: number; limit?: number }>(api.GET('/channels', { params: { query: f } })),
  })
}

export const useChannelGroups = (filters: ChannelFilters = {}, enabled = true) => {
  const preferred = useSession((s) => s.profile?.languages.join(',') ?? '')
  return useQuery({ enabled, queryKey: ['channel-groups', useProfileId(), filters, preferred], queryFn: () => unwrap<ChannelGroup[]>(api.GET('/channels/groups', { params: { query: filters } })) })
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

export const useHistory = () =>
  useQuery({ queryKey: ['history', useProfileId()], queryFn: () => unwrap<Tile[]>(api.GET('/history')) })

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
      for (const k of [qk.favorites, qk.home, ['channels'], ['title'], ['search'], ['browse'], ['browse-shelves'], ['history']]) void qc.invalidateQueries({ queryKey: k })
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
      for (const queryKey of [qk.profiles, ['current-profile'], qk.home, ['channels'], ['channel-groups'], ['search'], ['browse'], ['epg'], ['playback-options']]) void qc.invalidateQueries({ queryKey })
    },
  })
}

export const playbackOptions = (id: string, source_id?: string, signal?: AbortSignal) =>
  unwrap<PlaybackOptions>(api.GET('/playback/options', { signal, params: { query: { id, source_id } } }))
export const usePlaybackOptions = (id?: string) => useQuery({
  queryKey: ['playback-options', useProfileId(), id, useSession((s) => s.profile?.languages.join(','))],
  enabled: !!id,
  queryFn: ({ signal }) => playbackOptions(id!, undefined, signal),
})

/* ---------- requests & notices ---------- */
const REQUEST_VIEWS = [['requests'], ['title'], ['search'], ['discover'], qk.home] as const

// Approval and arrival happen elsewhere (another profile, the server job), so these lists poll.
export const useRequests = (scope: 'mine' | 'all', enabled = true) =>
  useQuery({
    queryKey: ['requests', useProfileId(), scope],
    enabled,
    staleTime: 0,
    refetchInterval: 60_000,
    queryFn: () => unwrap<RequestList>(api.GET('/requests', { params: { query: { scope } } })),
  })

export const useNotices = () =>
  useQuery({
    queryKey: ['notices', useProfileId()],
    staleTime: 0,
    refetchInterval: 60_000,
    queryFn: async () => (await unwrap<{ notices: Notice[] }>(api.GET('/notices'))).notices,
  })

export function useCreateRequest() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { media_type: 'movie' | 'tv'; tmdb_id: number; seasons?: number[] | null }) =>
      unwrap<MediaRequest>(api.POST('/requests', { body })),
    onSuccess: () => { for (const queryKey of REQUEST_VIEWS) void qc.invalidateQueries({ queryKey }) },
  })
}

export function useDecideRequest() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { id: string; approve: boolean; note?: string }) =>
      unwrap<MediaRequest>(api.POST(v.approve ? '/requests/{id}/approve' : '/requests/{id}/decline', { params: { path: { id: v.id } }, body: { note: v.note?.trim() || null } })),
    onSuccess: () => { for (const queryKey of REQUEST_VIEWS) void qc.invalidateQueries({ queryKey }) },
  })
}

export function useReadNotice() {
  const qc = useQueryClient()
  const profileId = useProfileId()
  return useMutation({
    mutationFn: (id: string) => unwrap<Notice>(api.POST('/notices/{id}/read', { params: { path: { id } } })),
    // The banner leaves at once; the server copy follows.
    onMutate: (id) => qc.setQueryData<Notice[]>(['notices', profileId], (list) => list?.map((n) => (n.id === id ? { ...n, read_at: new Date().toISOString() } : n))),
    onSettled: () => void qc.invalidateQueries({ queryKey: ['notices'] }),
  })
}

export const useRequestManager = (enabled: boolean) =>
  useQuery({ queryKey: ['request-manager', useProfileId()], enabled, queryFn: () => unwrap<RequestManagerSettings>(api.GET('/settings/request_manager')) })

/* ---------- watch rooms ---------- */
export const createRoom = (body: { id: string; variant_id?: string | null; audio_track?: number | null }) => unwrap<Room>(api.POST('/rooms', { body }))
export const joinRoom = (code: string, body: RoomJoin) => unwrap<RoomJoined>(api.POST('/rooms/{code}/join', { params: { path: { code } }, body }))
export const closeRoom = (code: string) => ok(api.DELETE('/rooms/{code}', { params: { path: { code } } }))
/** The join screen: who is in the room changes while it is open, so it polls. A refusal is an answer, not a fault to retry. */
export const useRoomInfo = (code: string | undefined, poll = true) =>
  useQuery({
    queryKey: ['room', useProfileId(), code],
    enabled: !!code,
    retry: false,
    staleTime: 0,
    refetchInterval: poll ? 5000 : false,
    queryFn: () => unwrap<Room>(api.GET('/rooms/{code}', { params: { path: { code: code! } } })),
  })

/* ---------- recordings ---------- */
const RECORDING_VIEWS = [['recordings'], ['recording-rules'], ['epg'], ['channels']] as const
const refreshRecordings = (qc: ReturnType<typeof useQueryClient>) => { for (const queryKey of RECORDING_VIEWS) void qc.invalidateQueries({ queryKey }) }

// The recorder starts, finishes and fails recordings on its own clock, so the Recordings screen polls;
// places that only ask whether recording is on (navigation, the guide) take what is cached.
export const useRecordings = (enabled = true, poll = true) =>
  useQuery({
    queryKey: ['recordings', useProfileId()],
    enabled,
    staleTime: poll ? 0 : 60_000,
    refetchInterval: poll ? 60_000 : false,
    queryFn: () => unwrap<RecordingList>(api.GET('/recordings')),
  })

export const useRecordingRules = (enabled = true) =>
  useQuery({ queryKey: ['recording-rules', useProfileId()], enabled, staleTime: 0, queryFn: () => unwrap<RecordingRuleList>(api.GET('/recording-rules')) })

export function useScheduleRecording() {
  const qc = useQueryClient()
  return useMutation({
    // All three: programme ids change with every guide refresh, channel and start find it again.
    mutationFn: (body: { programme_id: string; channel_id: string; start: string }) => unwrap<Recording>(api.POST('/recordings', { body })),
    onSettled: () => refreshRecordings(qc),
  })
}

export function useDeleteRecording() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => ok(api.DELETE('/recordings/{id}', { params: { path: { id } } })),
    onSettled: () => refreshRecordings(qc),
  })
}

export function useCreateRecordingRule() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { kind: RecordingRule['kind']; match: string; channel_id?: string | null; new_only?: boolean; keep?: number | null }) =>
      unwrap<RecordingRule>(api.POST('/recording-rules', { body })),
    onSettled: () => refreshRecordings(qc),
  })
}

export function usePatchRecordingRule() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { id: string; patch: { match?: string; channel_id?: string | null; new_only?: boolean; keep?: number | null; enabled?: boolean } }) =>
      unwrap<RecordingRule>(api.PATCH('/recording-rules/{id}', { params: { path: { id: v.id } }, body: v.patch })),
    onSettled: () => refreshRecordings(qc),
  })
}

export function useDeleteRecordingRule() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => ok(api.DELETE('/recording-rules/{id}', { params: { path: { id } } })),
    onSettled: () => refreshRecordings(qc),
  })
}
