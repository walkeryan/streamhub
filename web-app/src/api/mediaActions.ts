import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ok, unwrap } from './client'
import { useToggleWatchlist } from './hooks'
import type { Tile } from './types'
import { useMe } from './me'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'

export type MediaVote = 'up' | 'down' | null
type State = { id: string; vote: MediaVote; saved: boolean }

export function useMediaActions(tile: Tile, enabled = true) {
  const profile = useSession(s => s.profile?.id)
  const generation = useSession(s => s.generation)
  const me = useMe()
  const qc = useQueryClient()
  const toast = useUi(s => s.toast)
  const key = ['media-actions', profile, generation, tile.id] as const
  const state = useQuery({ queryKey: key, enabled: enabled && !!profile, staleTime: 30_000,
    queryFn: ({ signal }) => unwrap<State>(api.GET('/for-you/feedback', { params: { query: { id: tile.id } }, signal })) })
  const list = useToggleWatchlist()
  const active = () => Boolean(profile && useSession.getState().profile?.id === profile && useSession.getState().generation === generation)
  const invalidate = (names: readonly string[]) => {
    void qc.invalidateQueries({ predicate: query => names.includes(String(query.queryKey[0])) })
  }
  const catalog = ['home', 'for-you', 'for-you-together', 'browse', 'browse-shelves', 'browse-jumps', 'search', 'title', 'similar', 'watchlist', 'favorites', 'history', 'channels', 'sports', 'discover', 'genre', 'browse-categories']
  const setRating = async (vote: MediaVote) => {
    await ok(api.PUT('/for-you/feedback', { body: { id: tile.id, vote } }))
    if (!active()) return
    qc.setQueryData<State>(key, old => old ? { ...old, vote } : old)
    invalidate(['for-you', 'for-you-together', 'home'])
  }
  const setRemoval = async (removed: boolean) => {
    const params = { query: { id: tile.id } }
    await ok(removed ? api.DELETE('/library/media', { params }) : api.PUT('/library/media', { params }))
    if (active()) invalidate(catalog)
  }
  const perform = useMutation({ mutationFn: (action: () => Promise<void>) => action() })
  const run = async (action: () => Promise<void>) => {
    if (!active() || perform.isPending) return
    try { await perform.mutateAsync(action) }
    catch (err) { if (active()) toast(err instanceof Error ? err.message : 'Could not update this title.', 'error') }
  }
  return {
    vote: state.data?.vote ?? null, saved: state.data?.saved ?? false,
    canRemove: me.data?.role?.id === 'owner' && tile.sources.some(source => source.playable_in_app),
    ready: state.isSuccess, busy: perform.isPending, error: state.error, retry: () => void state.refetch(),
    rate: (vote: MediaVote) => run(async () => {
      const previous = state.data?.vote ?? null
      await setRating(vote)
      if (active()) toast(vote === 'up' ? 'Liked.' : vote === 'down' ? 'Disliked.' : 'Rating cleared.', 'success',
        { label: 'Undo', onSelect: () => run(() => setRating(previous)) })
    }),
    setSaved: (on: boolean) => run(async () => {
      await list.mutateAsync({ id: tile.id, on })
      if (!active()) return
      qc.setQueryData<State>(key, old => old ? { ...old, saved: on } : old)
      toast(on ? 'Added to My List.' : 'Removed from My List.', 'success')
    }),
    remove: () => run(async () => {
      await setRemoval(true)
      if (active()) toast('Removed from the household library.', 'success',
        { label: 'Undo', onSelect: () => run(() => setRemoval(false)) })
    }),
  }
}
