import { API_BASE, ApiError, authenticatedFetch } from './client'
import { useSession } from '../store/session'
import type { AuthTokens, Profile } from './types'

export interface PendingOwnerLogin { owner_pin_required: true; pending_token: string; profile: Profile; setup_required?: boolean }
export interface OwnerChallenge { challenge_id: string; poll_token: string; expires_in: number }
export interface OwnerAction { owner_action_token: string; profile_id: string; action: 'content_policy'; expires_in: number }
export interface SetupStatus { configured: boolean; local_approval_required: boolean; setup_authorized?: boolean; recovery_authorized?: boolean; recovery_request_id?: string; setup_request_id?: string; grant_id?: string; grant_secret?: string }
export class OwnerPinError extends ApiError {
  constructor(status: number, message: string, code?: string, public retryAt = 0) { super(status, message, code) }
}
export type OwnerPinResult = AuthTokens | OwnerAction
export async function ownerPinCall<T>(path: string, body: object | undefined, pendingToken?: string, signal?: AbortSignal): Promise<T> {
  const headers = new Headers({ 'content-type': 'application/json' })
  const token = pendingToken ?? useSession.getState().access
  if (token) headers.set('authorization', `Bearer ${token}`)
  const request = new Request(new URL(`${API_BASE}/auth/owner-pin/${path}`, location.origin), { method: body ? 'POST' : 'GET', headers, body: body ? JSON.stringify(body) : undefined, signal, cache: 'no-store' })
  // Never automatically resubmit a PIN attempt or enrollment. A 401 here may
  // mean an incorrect PIN; repeating it would consume a second allowed attempt.
  const response = await (!pendingToken && !['verify', 'setup', 'recover'].includes(path) ? authenticatedFetch(request) : fetch(request))
  if (!response.ok) {
    const data = await response.json().catch(() => ({})) as {code?:string;message?:string}
    const header = response.headers.get('Retry-After')
    const seconds = header && /^\d+$/.test(header) ? Number(header) : header ? Math.ceil((Date.parse(header) - Date.now()) / 1000) : 30
    throw new OwnerPinError(response.status, data.message ?? 'Owner authorization failed.', data.code, response.status === 429 ? Date.now() + Math.max(1, Number.isFinite(seconds) ? seconds : 30) * 1000 : 0)
  }
  return response.status === 204 ? undefined as T : await response.json() as T
}
export const isAuthTokens = (value: OwnerPinResult): value is AuthTokens => 'access_token' in value && 'refresh_token' in value && !!value.profile?.id
