import createClient from 'openapi-fetch'
import type { paths, AuthTokens, ProgressReport } from './types'
import { queryClient } from './queryClient'
import { useSession } from '../store/session'

export const API_BASE = '/api/v1'

export class ApiError extends Error {
  status: number
  code?: string
  /** The whole error body, for errors that say more than code and message (source_busy). */
  body?: unknown
  /** 403 forbidden: the permissions the caller lacks. */
  missing?: string[]
  /** 429: seconds until the server accepts another attempt. */
  retryAfter?: number
  constructor(status: number, message: string, code?: string, body?: unknown) {
    super(message)
    this.status = status
    this.code = code
    this.body = body
  }
}

/** Refusals about who the caller is read the same on every screen, whatever the server's wording. */
const REFUSALS: Record<string, string> = {
  approval_pending: 'Your account is waiting for approval.',
  user_suspended: 'This account has been suspended.',
  remote_access_denied: 'This account can only be used on the home network',
}
const SUSPENDED = 'This account has been suspended. Ask the person who runs this StreamHub to reinstate it.'
const REVOKED = 'This device was signed out. Sign in again to keep watching.'

function apiError(response: Response, body: unknown): ApiError {
  const e = (typeof body === 'object' && body !== null ? body : {}) as { message?: string; code?: string; missing?: unknown }
  const denied = e.code === 'forbidden' && Array.isArray(e.missing)
  const message = denied ? "You don't have permission to do this" : (e.code && REFUSALS[e.code]) || e.message || response.statusText || 'Request failed'
  const error = new ApiError(response.status, message, e.code, body)
  if (denied) error.missing = (e.missing as unknown[]).filter((m): m is string => typeof m === 'string')
  const wait = Number(response.headers.get('retry-after'))
  if (response.status === 429 && wait > 0) error.retryAfter = wait
  return error
}

export const ME_KEY = ['me'] as const
/** What the caller may do has changed, or may have: read /auth/me again. */
export const refreshMe = () => void queryClient.invalidateQueries({ queryKey: ME_KEY })

const refreshes = new Map<string, Promise<boolean>>()

async function refreshTokens(): Promise<boolean> {
  const { refresh, refreshSession, clear, generation } = useSession.getState()
  if (!refresh) return false
  const existing = refreshes.get(refresh)
  if (existing) return existing
  const pending = (async () => {
    try {
      const res = await fetch(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      })
      if (!res.ok) {
        // A suspended user's refresh is refused too; they are told why they are signed out.
        if ((await codeOf(res)) === 'user_suspended' && useSession.getState().refresh === refresh && useSession.getState().generation === generation) useSession.getState().end(SUSPENDED)
        throw new Error('refresh failed')
      }
      const t = (await res.json()) as AuthTokens
      // A pending refresh must not restore a logged-out or replaced profile.
      if (useSession.getState().refresh !== refresh) return false
      if (!refreshSession(t.access_token, t.refresh_token, t.profile, refresh, generation)) throw new Error('Refresh session changed')
      return true
    } catch {
      if (useSession.getState().refresh === refresh && useSession.getState().generation === generation) clear()
      return false
    } finally {
      window.setTimeout(() => refreshes.delete(refresh), 0)
    }
  })()
  refreshes.set(refresh, pending)
  return pending
}

function withAuth(req: Request): Request {
  const token = useSession.getState().access
  if (!token) return req
  const h = new Headers(req.headers)
  h.set('authorization', `Bearer ${token}`)
  return new Request(req, { headers: h })
}

async function codeOf(res: Response): Promise<string | undefined> {
  try { return ((await res.clone().json()) as { code?: string } | null)?.code } catch { return undefined }
}
// A wrong PIN is an answer, not an expired token: sending it again would spend a second attempt.
const NOT_A_TOKEN = new Set(['pin_required', 'pin_invalid'])

/**
 * fetch wrapper: bearer auth + single refresh-and-retry on 401. It also acts on what a refusal says about
 * the session itself: a suspended user is signed out with the reason, a stale session (role, permissions
 * or limits changed) is refreshed and re-reads /auth/me, a missing permission re-reads it too.
 */
export const authenticatedFetch = async (input: Request): Promise<Response> => {
  const initialProfile = useSession.getState().profile?.id
  const initialRefresh = useSession.getState().refresh
  const retry = input.clone()
  const res = await fetch(withAuth(input))
  if (res.status !== 401 && res.status !== 403) return res
  const code = await codeOf(res)
  const current = () => !!initialRefresh && useSession.getState().refresh === initialRefresh
  if (code === 'user_suspended') {
    if (current()) useSession.getState().end(SUSPENDED)
    return res
  }
  if (res.status === 403) {
    // What this user may do is not what the app thought: ask again (unless that was the asking).
    if (code === 'forbidden' && !new URL(input.url).pathname.endsWith('/auth/me')) refreshMe()
    return res
  }
  if (current() && !NOT_A_TOKEN.has(code ?? '')) {
    if (await refreshTokens() && useSession.getState().profile?.id === initialProfile) {
      if (code === 'session_stale') refreshMe()
      return fetch(withAuth(retry))
    }
    if (code === 'session_revoked' && !useSession.getState().access && !useSession.getState().notice) useSession.setState({ notice: REVOKED })
  }
  return res
}

export const api = createClient<paths>({ baseUrl: API_BASE, fetch: authenticatedFetch })
/** For the calls the contract gives no bearer token: what authorizes them is in the request itself (a room member's token). */
export const tokenless = createClient<paths>({ baseUrl: API_BASE })

/** Unwraps an openapi-fetch result, throwing ApiError on failure. */
export async function unwrap<T>(p: Promise<{ data?: unknown; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await p
  if (!response.ok) throw apiError(response, error)
  return data as T
}

/** For endpoints with no JSON body (204 etc.). */
export async function ok(p: Promise<{ error?: unknown; response: Response }>): Promise<void> {
  const { error, response } = await p
  if (!response.ok) throw apiError(response, error)
}

/**
 * For calls the generated contract does not describe yet. Same auth, refresh and errors as `api`.
 * A 404 means the server does not have the feature; callers hide what depends on it.
 */
export async function call<T>(method: 'GET' | 'POST' | 'PATCH', path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await authenticatedFetch(new Request(`${API_BASE}${path}`, {
    method, signal, headers: body === undefined ? undefined : { 'content-type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body),
  }))
  const text = await response.text()
  let data: unknown
  try { data = text ? JSON.parse(text) : undefined } catch { data = undefined }
  if (!response.ok) throw apiError(response, data)
  return data as T
}
/** The server has no such call (an older server). */
export const absent = (e: unknown): boolean => e instanceof ApiError && e.status === 404

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
