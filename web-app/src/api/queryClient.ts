import { QueryClient } from '@tanstack/react-query'

/** The app's one query cache. The API layer reaches it too: an answer that says the session's
 *  permissions changed re-reads who the caller is. */
export const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 15_000 } },
})
