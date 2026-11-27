import { create } from 'zustand'
import { requestId } from './requestId'
import { authenticatedFetch, API_BASE } from '../api/client'
import { titlePlay } from '../shellProtocol'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'
import type { CastCommand, CastPlayback } from '../player/castNative'

export type CastSession = {
  id: string; target_id: string; target_name: string; status: 'connected' | 'pending'
  playback: CastPlayback; last_ack: number; last_error: string | null
}
type CommandBody = {
  action: 'load' | 'play' | 'pause' | 'seek' | 'stop'; id?: string; variant_id?: string; source_id?: string
  audio_track?: number; position_ms?: number; catchup_programme_id?: string; rewind_at?: string; buffer_at?: string
}
export type ReceiverSync = { session: CastSession | null; controller_name: string | null; command: (CastCommand & { session_id?: string }) | null }
export async function castRequest<T>(path: string, method = 'GET', body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await authenticatedFetch(new Request(new URL(API_BASE + '/cast/' + path, window.location.origin), {
    method, signal, headers: body === undefined ? undefined : { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body),
  }))
  if (!response.ok) throw new Error(response.status === 403 ? 'You no longer have access to this TV.' : response.status === 409 ? 'The connection has changed. Check the TV and try again.' : 'Could not reach the TV. Check the connection and try again.')
  return response.status === 204 ? undefined as T : response.json() as Promise<T>
}
const scope = () => useSession.getState().profile?.id ?? ''
const sessionKey = () => 'sh.cast.selection:' + scope()
const hint = () => { try { return !!sessionStorage.getItem(sessionKey()) } catch { return false } }
const remember = (id: string | null) => {
  try { if (id) sessionStorage.setItem(sessionKey(), id); else sessionStorage.removeItem(sessionKey()) } catch { /* Storage is optional. */ }
}
type Outstanding = { session: string; request: string; payload: CommandBody; seq: number | null; at: number }
type ControllerState = { session: CastSession | null; restoring: boolean; problem: string | null; outstanding: Outstanding | null }
export const useCastController = create<ControllerState>(() => ({ session: null, restoring: hint(), problem: null, outstanding: null }))
export function setCastSession(next: CastSession | null) {
  const old = useCastController.getState()
  const outstanding = old.outstanding?.session === next?.id ? old.outstanding : null
  const complete = !!(next && outstanding?.seq && next.last_ack >= outstanding.seq)
  let problem: string | null = null
  if (complete && next?.last_error) problem = 'The TV could not complete that command.'
  else if (old.session && !next) problem = old.session.status === 'pending' ? 'The TV declined or the request expired.' : 'The TV disconnected.'
  else if (outstanding && Date.now() - outstanding.at > (outstanding.payload.action === 'load' ? 100_000 : 30_000)) problem = 'The TV has not confirmed the command. Retry or disconnect.'
  else if (outstanding?.seq === null || (!next && !old.session)) problem = old.problem
  remember(next?.id ?? null)
  useCastController.setState({ session: next, restoring: false, problem, outstanding: complete && !problem ? null : outstanding })
}
useSession.subscribe((next, prev) => {
  if (next.generation === prev.generation && next.profile?.id === prev.profile?.id) return
  try { sessionStorage.removeItem('sh.cast.selection:' + (prev.profile?.id ?? '')) } catch { /* Storage is optional. */ }
  useCastController.setState({ session: null, restoring: false, outstanding: null, problem: null })
})
export async function sendCastCommand(payload: CommandBody, retry = false) {
  const { session, outstanding } = useCastController.getState()
  if (!session || session.status !== 'connected') return
  if (!retry && outstanding) {
    useUi.getState().toast('Waiting for the TV. Open TV controls to retry or disconnect.')
    return
  }
  // If transport failed, resubmit the same idempotency key. After an acknowledged failure, a retry is a new command.
  const request = retry && outstanding && (!outstanding.seq || session.last_ack < outstanding.seq)
    ? outstanding.request : requestId()
  const action = { session: session.id, payload, request, seq: null, at: Date.now() }
  useCastController.setState({ outstanding: action, problem: null })
  try {
    const { seq } = await castRequest<{ seq: number }>('commands', 'POST', { session_id: session.id, request_id: request, ...payload })
    if (useCastController.getState().outstanding?.request === request) useCastController.setState({ outstanding: { ...action, seq } })
  } catch (error) {
    if (useCastController.getState().outstanding?.request === request) useCastController.setState({ problem: (error as Error).message })
  }
}
export function dispatchCastPlay(to: string): boolean {
  if (!to.startsWith('/play/')) return false
  const { session, restoring } = useCastController.getState()
  if (!restoring && !session) return false
  if (restoring || session?.status === 'pending') { useUi.getState().toast(restoring ? 'Reconnecting to your TV.' : 'Accept the connection on your TV.'); return true }
  const play = titlePlay(to, () => false)
  if (!play || play.kind !== 'title') return false
  const { id, source_id, variant_id, audio_track, start_position_ms, catchup_programme_id, rewind_at, buffer_at } = play
  void sendCastCommand({ action: 'load', id, source_id, variant_id, audio_track, position_ms: start_position_ms, catchup_programme_id, rewind_at, buffer_at })
  return true
}
