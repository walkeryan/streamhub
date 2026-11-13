import { ApiError } from '../api/client'
import { joinRoom } from '../api/hooks'
import type { PlaybackDescriptor, Room, RoomJoined } from '../api/types'
import { prefersHls } from '../player/usePlayback'
import { RoomClock } from './roomClock.ts'
import { RoomSocket, type RoomStateEvent } from './RoomSocket.ts'

/** ws(s)://<this origin><ws_path>?member_token=…: the member token is the only credential a socket URL carries. */
export function roomSocketUrl(joined: Pick<RoomJoined, 'ws_path' | 'member_token'>): string {
  const url = new URL(joined.ws_path, window.location.href)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.searchParams.set('member_token', joined.member_token)
  return url.toString()
}

/** A descriptor this young is handed out again instead of asking for another. */
const FRESH_MS = 3000

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
  private latest: { descriptor: PlaybackDescriptor; at: number }
  private refreshing: Promise<PlaybackDescriptor> | null = null
  private generation: string | undefined
  private readonly allowUnverifiedAudio: boolean
  private leaving: ReturnType<typeof setTimeout> | null = null
  private stateListeners = new Set<StateListener>()
  private errorListeners = new Set<ErrorListener>()

  constructor(room: Room, joined: RoomJoined, options: { generation?: string; allowUnverifiedAudio?: boolean; startAtMs?: number | null } = {}) {
    this.code = room.code
    this.room = room
    this.memberId = joined.member_id
    this.generation = options.generation
    this.allowUnverifiedAudio = !!options.allowUnverifiedAudio
    this.startAtMs = options.startAtMs ?? null
    this.latest = { descriptor: forRoom(joined.descriptor), at: Date.now() }
    this.clock.seed(room.server_ms)
    this.socket = new RoomSocket({
      url: roomSocketUrl(joined),
      clock: this.clock,
      onState: (state, previous) => { for (const listener of [...this.stateListeners]) listener(state, previous) },
      onError: (code, message) => { for (const listener of [...this.errorListeners]) listener(code, message) },
    })
  }

  onState(listener: StateListener): () => void { this.stateListeners.add(listener); return () => { this.stateListeners.delete(listener) } }
  onError(listener: ErrorListener): () => void { this.errorListeners.add(listener); return () => { this.errorListeners.delete(listener) } }

  /** usePlayback's descriptor source. Every attach wants a live descriptor: the join answer while it is fresh, a new one after. */
  descriptor = (generation?: string): Promise<PlaybackDescriptor> => {
    if (generation) this.generation = generation
    if (Date.now() - this.latest.at < FRESH_MS) return Promise.resolve(this.latest.descriptor)
    return this.refreshRoomDescriptor()
  }

  /**
   * The one way a member gets a new descriptor for the room: after a seek restarted the room's stream,
   * or when the player has to attach again. There is no call that renews a seat's descriptor, so this
   * joins again: that answers with a new seat, and the socket moves to it (leaving the old one).
   */
  refreshRoomDescriptor = (): Promise<PlaybackDescriptor> => {
    this.refreshing ??= (async () => {
      try {
        const joined = await this.joinAgain()
        this.memberId = joined.member_id
        this.socket.moveTo(roomSocketUrl(joined))
        this.latest = { descriptor: forRoom(joined.descriptor), at: Date.now() }
        return this.latest.descriptor
      } finally { this.refreshing = null }
    })()
    return this.refreshing
  }

  private async joinAgain(): Promise<RoomJoined> {
    const body = { client: 'web' as const, prefer_hls: prefersHls(), recovery_generation: this.generation ?? null, allow_unverified_audio: this.allowUnverifiedAudio }
    try { return await joinRoom(this.code, body) } catch (e) {
      // A full room has no second seat for a member who already holds one: give that one up, then ask again.
      if (!(e instanceof ApiError) || e.code !== 'room_full') throw e
      this.socket.vacate()
      return joinRoom(this.code, body)
    }
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
}

/** The room sets the position; the profile's own resume point must not pull the player away from it. */
const forRoom = (descriptor: PlaybackDescriptor): PlaybackDescriptor => ({ ...descriptor, resume_position_ms: 0 })
