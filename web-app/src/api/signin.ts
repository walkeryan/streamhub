import { ApiError, API_BASE, api, ok, unwrap } from './client'
import type { AuthTokens } from './types'
import type { components } from './schema'

type S = components['schemas']
export type SignInMethod = S['SignInMethod']
export type PendingApproval = S['PendingApproval']
/** What a finished Plex sign-in answers: a session, a wait for approval, or (started from Settings) a link. */
export type PlexOutcome = (AuthTokens & { status: 'active' }) | PendingApproval | { status: 'linked' }
export interface InviteLookup { valid: boolean; display_name?: string | null; username?: string | null; setup: boolean }

export const METHOD_NAME: Record<SignInMethod, string> = { google: 'Google', plex: 'Plex', password: 'Username and password' }
// The server's own rules, said before the request is sent; the server still decides.
export const USERNAME = /^[a-z0-9._-]{3,32}$/
export const PASSWORD_MIN = 10
export function passwordProblem(username: string, password: string, again?: string): string | null {
  if (password.length < PASSWORD_MIN) return `Use at least ${PASSWORD_MIN} characters.`
  if (username && password.toLowerCase().includes(username.toLowerCase())) return 'The password cannot contain the username.'
  if (again !== undefined && again !== password) return 'The two passwords are not the same.'
  return null
}
export const usernameProblem = (username: string): string | null =>
  USERNAME.test(username.trim().toLowerCase()) ? null : 'A username is 3 to 32 letters, digits, dots, dashes or underscores.'

/** How long a 429 asks to wait, in words. */
export function waitText(seconds: number | undefined): string {
  if (!seconds) return 'Too many attempts. Try again later.'
  return seconds < 90 ? `Too many attempts. Try again in ${seconds} seconds.` : `Too many attempts. Try again in ${Math.ceil(seconds / 60)} minutes.`
}
/** A refusal of a sign-in or of a change to sign-in methods, in words the person can act on. */
export function signInError(e: unknown, fallback: string): string {
  if (!(e instanceof ApiError)) return e instanceof Error && e.message ? e.message : fallback
  if (e.status === 429) return waitText(e.retryAfter)
  switch (e.code) {
    case 'invalid_credentials': return 'That username or password is not right.'
    case 'user_suspended': return 'This account has been suspended. Ask the person who runs this StreamHub to reinstate it.'
    case 'invite_required': return 'New people need an invitation here. Ask the person who runs this StreamHub for an invite link.'
    case 'invite_invalid': return 'This link can no longer be used: it has expired, been used already or been withdrawn. Ask for a new one.'
    case 'username_taken': return 'That username is taken. Choose another.'
    case 'sign_in_expired': return 'That sign-in was not finished or took too long. Please start again.'
    case 'plex_unavailable': return 'Plex could not be reached. Try again in a moment.'
    case 'identity_in_use': return 'That account already signs in as someone else here.'
    case 'already_linked': return 'Another account of that kind is linked already. Unlink it first.'
    case 'last_method': return 'This is the only way left to sign in, so it cannot be removed.'
    case 'wrong_password': return 'That is not your current password.'
    case 'shared_device': return 'Sign-in methods cannot be changed on a shared device.'
    case 'password_disabled': case 'plex_disabled': return 'That way of signing in is switched off on this server.'
    default: return e.message || fallback
  }
}

/** Ask the server for Plex's sign-in page and go there. Plex sends the browser back to `returnTo` with ?plex=<state>. */
export async function goToPlex(returnTo: string, more: { invite?: string; link?: boolean } = {}): Promise<never> {
  const { auth_url } = await unwrap<{ auth_url: string }>(api.POST('/auth/plex/start', { body: { return_to: returnTo, ...more } }))
  window.location.assign(auth_url)
  return new Promise<never>(() => undefined)
}
// React StrictMode runs effects twice: a state Plex handed back is spent by its first use.
const finishing = new Map<string, Promise<PlexOutcome>>()
export function finishPlex(state: string): Promise<PlexOutcome> {
  let pending = finishing.get(state)
  if (!pending) {
    pending = unwrap<PlexOutcome>(api.POST('/auth/plex/complete', { body: { state } }))
    finishing.set(state, pending)
  }
  return pending
}
/** The state Plex's redirect came back with, taken out of the address bar so a reload does not replay it. */
export function takePlexState(): string | null {
  const url = new URL(window.location.href)
  const state = url.searchParams.get('plex')
  if (state === null) return null
  url.searchParams.delete('plex')
  window.history.replaceState(window.history.state, '', url.pathname + url.search)
  return state
}

export const signin = {
  password: (username: string, password: string) => unwrap<AuthTokens>(api.POST('/auth/password', { body: { username, password } })),
  /** Set a first password (with a username) or change the current one. */
  setPassword: (body: { new_password: string; current_password?: string; username?: string }) => ok(api.PUT('/auth/password', { body })),
  unlink: (method: SignInMethod) => ok(api.DELETE('/auth/methods/{method}', { params: { path: { method } } })),
  /** Google's sign-in page for linking; Google returns the browser to /settings?linked=google. */
  linkGoogle: async (): Promise<never> => {
    const { auth_url } = await unwrap<{ auth_url: string }>(api.POST('/auth/google/link'))
    window.location.assign(auth_url)
    return new Promise<never>(() => undefined)
  },
  acceptInvite: (token: string, username: string, password: string) => unwrap<AuthTokens>(api.POST('/auth/invite/accept', { body: { token, username, password } })),
  /** What the link is. `null`: a server from before the question could be asked, where only a sign-in can tell. */
  lookupInvite: async (token: string, signal?: AbortSignal): Promise<InviteLookup | null> => {
    const response = await fetch(`${API_BASE}/auth/invite/lookup`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ token }), signal })
    if (response.status === 404 || response.status === 405) return null
    if (response.status === 429) throw new Error(waitText(Number(response.headers.get('retry-after')) || undefined))
    if (!response.ok) throw new Error('This link cannot be checked right now. Please retry.')
    return (await response.json()) as InviteLookup
  },
}
