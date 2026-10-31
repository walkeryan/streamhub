import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from './client'
import type { BrowseFilters } from './hooks'
import { useSession } from '../store/session'

export function useBrowseGenres(section: string, filters: BrowseFilters) {
  const profile = useSession((s) => s.profile?.id ?? '')
  return useQuery({ queryKey: ['browse-genres', profile, section, filters],
    queryFn: () => unwrap<Array<{ name: string; count: number }>>(api.GET('/browse/{section}/genres', { params: { path: { section }, query: filters } })),
  })
}
