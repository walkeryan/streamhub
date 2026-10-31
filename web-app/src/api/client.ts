import createClient from 'openapi-fetch'
import type { paths, AuthTokens, ProgressReport } from './types'
import { useSession } from '../store/session'

export const API_BASE = '/api/v1'

export class ApiError extends Error {
  status: number
  code?: string
  constructor(status: number, message: string, code?: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

let refreshing: Promise<boolean> | null = null

async function refreshTokens(): Promise<boolean> {
  const { refresh, setSession, clear } = useSession.getState()
  if (!refresh) return false
  refreshing ??= (async () => {
    try {
      const res = await fetch(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      })
      if (!res.ok) throw new Error('refresh failed')
      const t = (await res.json()) as AuthTokens
      setSession(t.access_token, t.refresh_token, t.profile)
      return true
    } catch {
      clear()
      return false
    } finally {
      window.setTimeout(() => (refreshing = null), 0)
    }
  })()
  return refreshing
}

function withAuth(req: Request): Request {
  const token = useSession.getState().access
  if (!token) return req
  const h = new Headers(req.headers)
  h.set('authorization', `Bearer ${token}`)
  return new Request(req, { headers: h })
}

/** fetch wrapper: bearer auth + single refresh-and-retry on 401. */
const authedFetch = async (input: Request): Promise<Response> => {
  const retry = input.clone()
  const res = await fetch(withAuth(input))
  if (res.status === 401 && useSession.getState().refresh) {
    if (await refreshTokens()) return fetch(withAuth(retry))
  }
  return res
}

export const api = createClient<paths>({ baseUrl: API_BASE, fetch: authedFetch })

/** Unwraps an openapi-fetch result, throwing ApiError on failure. */
export async function unwrap<T>(p: Promise<{ data?: unknown; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await p
  if (!response.ok) {
    const e = error as { message?: string; code?: string } | undefined
    throw new ApiError(response.status, e?.message ?? response.statusText ?? 'Request failed', e?.code)
  }
  return data as T
}

/** For endpoints with no JSON body (204 etc.). */
export async function ok(p: Promise<{ error?: unknown; response: Response }>): Promise<void> {
  const { error, response } = await p
  if (!response.ok) {
    const e = error as { message?: string; code?: string } | undefined
    throw new ApiError(response.status, e?.message ?? response.statusText ?? 'Request failed', e?.code)
  }
}

/**
 * Progress must survive page unload. Browsers cannot attach an Authorization header to
 * navigator.sendBeacon, so unload reports use fetch({keepalive:true}) (same delivery guarantee, supports headers).
 */
export function reportProgress(body: ProgressReport, unloading = false): void {
  const token = useSession.getState().access
  const url = `${API_BASE}/playback/progress`
  const json = JSON.stringify(body)
  try {
    void fetch(url, {
      method: 'POST',
      keepalive: unloading,
      headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) },
      body: json,
    }).catch(() => undefined)
  } catch {
    if (unloading && navigator.sendBeacon) navigator.sendBeacon(url, new Blob([json], { type: 'application/json' }))
  }
}
