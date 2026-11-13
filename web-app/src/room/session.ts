import { ApiError } from '../api/client'
import { joinRoom, roomDescriptor } from '../api/hooks'
import type { PlaybackDescriptor, Room, RoomJoined } from '../api/types'
import { prefersHls } from '../player/usePlayback'
import { RoomClock } from './roomClock.ts'
import { positionAt } from './roomSync.ts'
import { RoomSocket, type RoomStateEvent } from './RoomSocket.ts'

/** ws(s)://<this origin><ws_path>?member_token=…: the member token is the only credential a socket URL carries. */
export function roomSocketUrl(joined: Pick<RoomJoined, 'ws_path' | 'member_token'>): string {
  const url = new URL(joined.ws_path, window.location.href)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.searchParams.set('member_token', joined.member_token)
  return url.toString()
}

/**
 * The member token of the seat this tab holds in each room. A join that carries it keeps the seat
 * (coming back after a reload or a lost connection) instead of adding a member. Kept per tab: two
 * tabs are two members.
 */
const SEATS = 'sh.roomSeats.v1'
const seats = (): Record<string, string> => { try { return JSON.parse(sessionStorage.getItem(SEATS) ?? '{}') as Record<string, string> } catch { return {} } }
export const seatToken = (code: string): string | null => seats()[code] ?? null
function keepSeat(code: string, token: string | null): void {
  try {
    const all = seats()
    if (token) all[code] = token; else delete all[code]
    sessionStorage.setItem(SEATS, JSON.stringify(all))
  } catch { /* storage unavailable: the next join takes a new seat */ }
}

/** A member token is renewed once this much of its life has passed, so a long film does not outlast it. */
const RENEW_AT = 0.8
const RENEW_RETRY_S = 60
/** What the descriptor call says when the seat itself is gone, as the reason the room ended for this member. */
const SEAT_LOST: Record<string, string> = { room_unauthorized: 'unauthorized', not_a_member: 'not_a_member', room_not_found: 'not_found' }

type StateListener = (state: RoomStateEvent, previous: RoomStateEvent | null) => void
type ErrorListener = (code: string, message: string) => void

/**
 * One member's seat in a watch room: the socket, the server clock and the descriptor the player uses.
 * The room, not the member, decides what plays and where, so the player takes its descriptor from
 * here (`descriptor`) rather than from /playback/resolve.
 */
export class RoomSession {
  readonly code: string
  readonly room: Room
  readonly clock = new RoomClock()
  readonly socket: RoomSocket
  memberId: string
  /** Seek the room here once the socket is open (a host who moved a title they were already watching into a room). */
  startAtMs: number | null
  private memberToken: string
  private latest: PlaybackDescriptor
  /** `latest` has not been given to the player yet: the join's answer, or one just fetched or followed. */
  private unused = true
  private refreshing: Promise<PlaybackDescriptor> | null = null
  private generation: string | undefined
  private readonly allowUnverifiedAudio: boolean
  private leaving: ReturnType<typeof setTimeout> | null = null
  private renewal: ReturnType<typeof setTimeout> | null = null
  private stateListeners = new Set<StateListener>()
  private errorListeners = new Set<ErrorListener>()

  constructor(room: Room, joined: RoomJoined, options: { generation?: string; allowUnverifiedAudio?: boolean; startAtMs?: number | null } = {}) {
    this.code = room.code
    this.room = room
    this.memberId = joined.member_id
    this.memberToken = joined.member_token
    this.generation = options.generation
    this.allowUnverifiedAudio = !!options.allowUnverifiedAudio
    this.startAtMs = options.startAtMs ?? null
    this.latest = joined.descriptor
    this.clock.seed(room.server_ms)
    this.socket = new RoomSocket({
      url: roomSocketUrl(joined),
      clock: this.clock,
      onState: (state, previous) => { for (const listener of [...this.stateListeners]) listener(state, previous) },
      onError: (code, message) => { for (const listener of [...this.errorListeners]) listener(code, message) },
    })
    keepSeat(this.code, this.memberToken)
    // However the room ends for this member, the seat is not one to come back to.
    this.socket.subscribe(() => { if (this.socket.snapshot.closed) this.forget() })
    this.renewIn(joined.expires_in_seconds * RENEW_AT)
  }

  onState(listener: StateListener): () => void { this.stateListeners.add(listener); return () => { this.stateListeners.delete(listener) } }
  onError(listener: ErrorListener): () => void { this.errorListeners.add(listener); return () => { this.errorListeners.delete(listener) } }

  /** Which start of the room's one shared stream the descriptor describes; null when each member plays the title for themselves. */
  get streamEpoch(): number | null { return this.latest.stream_epoch ?? null }

  /** usePlayback's descriptor source. The join's answer serves the first attach; every later one asks how the seat stands now. */
  descriptor = async (generation?: string): Promise<PlaybackDescriptor> => {
    if (generation) this.generation = generation
    if (!this.unused) await this.refreshRoomDescriptor()
    this.unused = false
    return this.latest
  }

  /**
   * The member's descriptor as the server has it now, for a player that lost its place: an error, a
   * stream of its own that does not reach the room's position, an expired URL. The seat is unchanged;
   * this never joins again.
   */
  refreshRoomDescriptor = (): Promise<PlaybackDescriptor> => {
    this.refreshing ??= (async () => {
      try {
        this.latest = await roomDescriptor(this.code, {
          member_token: this.memberToken, client: 'web', prefer_hls: prefersHls(), recovery_generation: this.generation, allow_unverified_audio: this.allowUnverifiedAudio,
        })
        this.unused = true
        return this.latest
      } catch (e) {
        // Without the seat there is nothing to play: the room ends here, with Rejoin where that can help.
        const lost = e instanceof ApiError ? SEAT_LOST[e.code ?? ''] : undefined
        if (lost) this.socket.end(lost)
        throw e
      } finally { this.refreshing = null }
    })()
    return this.refreshing
  }

  /**
   * The room's shared stream was started again (someone seeked): the playlist URL is the one already
   * held, its time zero is now `stream_start_ms`. False when this member has no seat on such a stream.
   */
  followStream(state: RoomStateEvent): boolean {
    if (state.stream_epoch === null || this.latest.stream_epoch == null) return false
    this.latest = {
      ...this.latest, stream_epoch: state.stream_epoch, start_position_ms: state.stream_start_ms ?? this.latest.start_position_ms,
      resume_position_ms: Math.round(positionAt(state, this.clock.serverNow()) ?? 0),
    }
    this.unused = true
    return true
  }

  private renewIn(seconds: number): void {
    if (this.renewal !== null) clearTimeout(this.renewal)
    this.renewal = setTimeout(() => { this.renewal = null; void this.renew() }, Math.max(1, seconds) * 1000)
  }
  /** Joining with the member token keeps the seat and answers with a token that lasts another two hours. */
  private async renew(): Promise<void> {
    if (this.socket.snapshot.closed) return
    try {
      const joined = await joinRoom(this.code, {
        client: 'web', prefer_hls: prefersHls(), recovery_generation: this.generation ?? null, allow_unverified_audio: this.allowUnverifiedAudio, member_token: this.memberToken,
      })
      if (this.socket.snapshot.closed) return
      this.memberId = joined.member_id
      this.memberToken = joined.member_token
      this.socket.setUrl(roomSocketUrl(joined))
      keepSeat(this.code, this.memberToken)
      this.renewIn(joined.expires_in_seconds * RENEW_AT)
    } catch { this.renewIn(RENEW_RETRY_S) }
  }

  /** Mounting keeps the seat; unmounting gives it up a moment later, unless mounted again first (a development double mount). */
  retain(): void {
    if (this.leaving !== null) { clearTimeout(this.leaving); this.leaving = null }
    this.socket.connect()
  }
  release(): void {
    if (this.leaving !== null) return
    this.leaving = setTimeout(() => this.socket.leave(), 0)
  }
  private forget(): void {
    if (this.renewal !== null) { clearTimeout(this.renewal); this.renewal = null }
    if (seatToken(this.code) === this.memberToken) keepSeat(this.code, null)
  }
}
