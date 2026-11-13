import { useInfiniteQuery, useMutation, useQueries, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query'
import { z } from 'zod'
import { api, ok, tokenless, unwrap } from './client'
import { useCan } from './me'
import { useSession } from '../store/session'
import { mergeProgrammes } from '../lib/guideWindow'
import { isOpenRequest, refetchMsFor, tileRequest, tilesRefetchMs } from '../lib/requests'
import { MIN_QUERY } from '../lib/searchQuery'
import type { PlaybackDescriptor, Recording, RecordingList, RecordingRule, RecordingRuleList, Room, RoomDescriptorQuery, RoomJoin, RoomJoined } from './types'
import type {
  PlaybackOptions, BrowsePage, ChannelGroup, ChannelsResult, Disclaimer, EpgData, HomeData, LanguageCount, Profile, Programme, SearchResult, SearchSectionName,
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

export const useHome = () => useQuery({
  queryKey: [...qk.home, useProfileId()],
  queryFn: () => unwrap<HomeData>(api.GET('/home')),
  staleTime: 30_000,
  // Only while one of its posters shows a request on its way, so that chip and bar keep up.
  refetchInterval: (query) => tilesRefetchMs([...(query.state.data?.hero ?? []), ...(query.state.data?.rows ?? []).flatMap((row) => row.tiles)]),
})

/** A server from before the unified search sends titles and channels only: read it as the same shape. */
function completeSearch(r: Partial<SearchResult>): SearchResult {
  const results = r.results ?? [...(r.play_here ?? []), ...(r.open_in_app ?? [])]
  const channels = r.channels ?? [], programmes = r.programmes ?? []
  const all = (rows: unknown[]) => ({ count: rows.length, more: false })
  return {
    ...r, results, channels, programmes, play_here: r.play_here ?? [], open_in_app: r.open_in_app ?? [],
    sections: r.sections ?? { titles: all(results), channels: all(channels), programmes: all(programmes) },
    order: r.order ?? ['programmes', 'channels', 'titles'], attribution: r.attribution ?? 'Availability data by JustWatch',
  }
}

/** One search across titles, channels and guide broadcasts. `expand` names the sections asked for in full
 *  ("Show more"); the page shown so far stays up while the fuller one, or the next query, loads. */
export const useSearch = (q: string, expand: readonly SearchSectionName[] = []) => {
  const profile = useProfileId()
  const key = q.trim().toLowerCase().replace(/\s+/g, ' ')
  return useQuery<SearchResult>({
    queryKey: ['search', profile, key, [...expand].sort().join(','), useSession((s) => s.profile?.languages.join(','))],
    enabled: key.length >= MIN_QUERY,
    staleTime: 30_000,
    placeholderData: useProfilePlaceholder<SearchResult>(),
    queryFn: async ({ signal }) => completeSearch(await unwrap<Partial<SearchResult>>(api.GET('/search', { signal, params: { query: { q: q.trim(), ...(expand.length ? { expand: [...expand].sort().join(',') } : {}) } } }))),
  })
}

export const useTitle = (id: string | undefined) =>
  useQuery({
    queryKey: ['title', useProfileId(), id],
    enabled: !!id,
    // While a request for it is on its way the page follows it, at the pace of its phase; otherwise it is read once.
    refetchInterval: (query) => {
      const request = query.state.data ? tileRequest(query.state.data) : null
      return request ? refetchMsFor([request.phase]) : false
    },
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
export const useBrowseShelves = (section: string, f: { language?: string; limit_rows?: number; limit_items?: number; include_adult?: boolean }, enabled = true) =>
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

type ChannelPage = ChannelsResult & { total?: number; offset?: number; limit?: number }
/** The guide's channels, a page at a time: the next page starts where the loaded ones end. */
export const useChannelPages = (f: ChannelFilters, limit: number) => {
  const preferred = useSession((s) => s.profile?.languages.join(',') ?? '')
  return useInfiniteQuery({
    queryKey: ['channels', useProfileId(), { ...f, limit }, preferred, 'pages'],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => unwrap<ChannelPage>(api.GET('/channels', { params: { query: { ...f, limit, offset: pageParam } } })),
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((n, p) => n + (p.channels?.length ?? 0), 0)
      // A server that sends no total is asked again for as long as its pages come back full.
      const more = last.total != null ? loaded < last.total : (last.channels?.length ?? 0) >= limit
      return more && last.channels?.length ? loaded : undefined
    },
  })
}

export const useChannelGroups = (filters: ChannelFilters = {}, enabled = true) => {
  const preferred = useSession((s) => s.profile?.languages.join(',') ?? '')
  return useQuery({ enabled, queryKey: ['channel-groups', useProfileId(), filters, preferred], queryFn: () => unwrap<ChannelGroup[]>(api.GET('/channels/groups', { params: { query: filters } })) })
}

export const useLanguages = () =>
  useQuery({ queryKey: ['channel-languages', useProfileId()], queryFn: () => unwrap<LanguageCount[]>(api.GET('/channels/languages')) })

export interface EpgRequest { ids: string[]; from: number; to: number; enabled: boolean }
export interface EpgChunks { byChannel: Record<string, Programme[]>; loading: boolean[]; error: unknown }
// Not a closure: the same function on every render is what lets the merged schedule keep its identity until an answer changes.
const mergeEpg = (results: UseQueryResult<EpgData>[]): EpgChunks => {
  const lists = new Map<string, Programme[][]>()
  for (const r of results) for (const [id, list] of Object.entries(r.data ?? {})) lists.set(id, [...(lists.get(id) ?? []), list])
  return {
    byChannel: Object.fromEntries([...lists].map(([id, parts]) => [id, mergeProgrammes(parts)])),
    loading: results.map((r) => r.isLoading),
    error: results.find((r) => r.error)?.error ?? null,
  }
}
/**
 * The guide's programmes, asked for by page of channels and by span of hours. Every request is cached
 * on its own, so loading more channels or later hours never asks again for what is on screen.
 */
export const useEpgChunks = (requests: EpgRequest[]): EpgChunks => {
  const profile = useProfileId()
  return useQueries({
    queries: requests.map((r) => {
      const from = new Date(r.from).toISOString(), to = new Date(r.to).toISOString()
      return {
        queryKey: ['epg', profile, r.ids.join(','), from, to],
        enabled: r.enabled && r.ids.length > 0,
        staleTime: 60_000,
        queryFn: () => unwrap<EpgData>(api.GET('/epg', { params: { query: { channel_ids: r.ids.join(','), from, to } } })),
      }
    }),
    combine: mergeEpg,
  })
}

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

// Approval, downloading and arrival happen elsewhere (another profile, the request manager), so these
// lists poll: as often as the busiest request in them needs, and (react-query's default, said here
// because it is relied on) not at all while the tab is hidden.
export const useRequests = (scope: 'mine' | 'all', enabled = true) =>
  useQuery({
    queryKey: ['requests', useProfileId(), scope],
    enabled,
    staleTime: 0,
    refetchInterval: (query) => refetchMsFor((query.state.data?.requests ?? []).map((r) => r.progress.phase)),
    refetchIntervalInBackground: false,
    queryFn: () => unwrap<RequestList>(api.GET('/requests', { params: { query: { scope } } })),
  })

/**
 * The open request for a title, with everything the server knows about it (the title itself carries only
 * the phase and the percentage). Found among the requests this profile may list; undefined when it is
 * somebody else's and this profile may not see those.
 */
export function useOpenRequestFor(titleId: string | undefined, enabled: boolean): MediaRequest | undefined {
  const all = useCan('requests.approve')
  const { data } = useRequests(all ? 'all' : 'mine', enabled && !!titleId)
  return enabled ? data?.requests.find((r) => r.title_id === titleId && isOpenRequest(r.state)) : undefined
}

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

/** "Check again": the server asks the request manager about this request now, or sends an unsent one again. */
export function useRefreshRequest() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => unwrap<MediaRequest>(api.POST('/requests/{id}/refresh', { params: { path: { id } } })),
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
/** A member's descriptor as it is now. The member token is the credential; the seat does not change. */
export const roomDescriptor = (code: string, query: RoomDescriptorQuery) => unwrap<PlaybackDescriptor>(tokenless.GET('/rooms/{code}/descriptor', { params: { path: { code }, query } }))
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
const RECORDING_VIEWS = [['recordings'], ['recording-rules'], ['epg'], ['channels'], ['search']] as const
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
