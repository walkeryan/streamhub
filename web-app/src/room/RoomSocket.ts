import type { RoomClock } from './roomClock.ts'
import type { RoomClockState } from './roomSync.ts'

/**
 * A member's connection to a watch room (contract: RoomJoined). Sends this member's commands, keeps
 * the latest members, room clock, waiting notice and lock, measures the server clock with a ping every
 * five seconds, and reconnects with the same member token when the connection drops: the server keeps
 * the seat for 20 s of silence and resynchronises a returning socket with members, state and waiting.
 */
export interface RoomMember { id: string; name: string; host: boolean; buffering: boolean }
export interface RoomStateEvent extends RoomClockState {
  /** the member who caused it, or null when the room did */
  by: string | null
  controls_locked: boolean
  /** A room on one shared stream: which start of it this state describes, and where in the title that start is. Null in other rooms. */
  stream_epoch: number | null
  stream_start_ms: number | null
}
export type RoomStatus = 'connecting' | 'open' | 'reconnecting' | 'closed'
export interface RoomSnapshot {
  status: RoomStatus
  members: RoomMember[]
  state: RoomStateEvent | null
  /** the member the room is paused for while they buffer */
  waiting: string | null
  controlsLocked: boolean
  /** why the room ended for this member; set once and final */
  closed: string | null
}

/** The part of WebSocket this uses: the seam where tests put a fake. */
export interface SocketLike {
  send(data: string): void
  close(code?: number, reason?: string): void
  onopen: ((event: unknown) => void) | null
  onmessage: ((event: { data: unknown }) => void) | null
  onclose: ((event: { code?: number }) => void) | null
  onerror: ((event: unknown) => void) | null
}

export interface RoomSocketOptions {
  /** ws(s)://…/rooms/<code>/ws?member_token=… */
  url: string
  clock: RoomClock
  createSocket?: (url: string) => SocketLike
  now?: () => number
  /** Every room clock, with the one before it (null for the first after a connect). */
  onState?: (state: RoomStateEvent, previous: RoomStateEvent | null) => void
  /** A command the server refused: controls_locked, host_only, live_no_seek, bad_message. */
  onError?: (code: string, message: string) => void
}

export const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 15_000]
export const PING_INTERVAL_MS = 5000
/** Close codes after which the server will not take this member back. */
const FINAL_CODES: Record<number, string> = { 4000: 'closed', 4401: 'unauthorized', 4404: 'not_found' }
const LEFT = 1000

export class RoomSocket {
  private readonly clock: RoomClock
  private readonly createSocket: (url: string) => SocketLike
  private readonly now: () => number
  private readonly onState?: RoomSocketOptions['onState']
  private readonly onError?: RoomSocketOptions['onError']
  private url: string
  private socket: SocketLike | null = null
  private opened = false
  private attempt = 0
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private pingTimer: ReturnType<typeof setInterval> | null = null
  private closedReason: string | null = null
  private buffering = false
  private listeners = new Set<() => void>()
  private current: RoomSnapshot = { status: 'connecting', members: [], state: null, waiting: null, controlsLocked: false, closed: null }

  constructor(options: RoomSocketOptions) {
    this.url = options.url
    this.clock = options.clock
    this.createSocket = options.createSocket ?? ((url) => new WebSocket(url) as unknown as SocketLike)
    this.now = options.now ?? Date.now
    this.onState = options.onState
    this.onError = options.onError
  }

  /** Stable between changes, so it can back useSyncExternalStore. */
  get snapshot(): RoomSnapshot { return this.current }
  subscribe(listener: () => void): () => void {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }

  connect(): void {
    if (this.socket || this.current.closed) return
    this.open()
  }

  play(): boolean { return this.send({ type: 'play' }) }
  pause(): boolean { return this.send({ type: 'pause' }) }
  seek(positionMs: number): boolean { return this.send({ type: 'seek', position_ms: Math.max(0, Math.round(positionMs)) }) }
  /** Host only. */
  lock(on: boolean): boolean { return this.send({ type: 'lock', on }) }
  /** This member's player is stalled (true) or has recovered (false). Repeated after a reconnect. */
  setBuffering(on: boolean): void {
    if (this.buffering === on) return
    this.buffering = on
    this.send({ type: 'buffering', on })
  }

  /** A renewed member token: the open connection stays, the next one is made with it. */
  setUrl(url: string): void { this.url = url }

  /** The seat turned out to be gone (the server said so elsewhere than on this socket): the room ends for this member. */
  end(reason: string): void { this.finish(reason) }

  /** Leave the room at once (close code 1000); nothing reconnects afterwards. */
  leave(): void { this.finish('left', LEFT) }

  private send(message: Record<string, unknown>): boolean {
    if (!this.socket || !this.opened) return false
    try { this.socket.send(JSON.stringify(message)); return true } catch { return false }
  }

  private update(patch: Partial<RoomSnapshot>): void {
    this.current = { ...this.current, ...patch }
    for (const listener of [...this.listeners]) listener()
  }

  private open(): void {
    if (this.retryTimer !== null) { clearTimeout(this.retryTimer); this.retryTimer = null }
    this.closedReason = null
    let socket: SocketLike
    try { socket = this.createSocket(this.url) } catch { this.retry(); return }
    this.socket = socket
    this.opened = false
    socket.onopen = () => {
      if (this.socket !== socket) return
      this.opened = true
      this.update({ status: 'open' })
      this.ping()
      this.pingTimer = setInterval(() => this.ping(), PING_INTERVAL_MS)
      if (this.buffering) this.send({ type: 'buffering', on: true })
    }
    socket.onmessage = (event) => { if (this.socket === socket) this.receive(event.data) }
    socket.onerror = () => undefined // a close always follows; that is where the retry is decided
    socket.onclose = (event) => {
      if (this.socket !== socket) return
      const code = event?.code ?? 1006
      this.drop()
      // The server said why (closed{reason}) or used a code that means the seat is gone: do not knock again.
      const final = this.closedReason ?? FINAL_CODES[code]
      if (final) this.finish(final)
      else this.retry()
    }
  }

  /** Detach from the current socket; `code` also closes it. */
  private drop(code?: number): void {
    if (this.pingTimer !== null) { clearInterval(this.pingTimer); this.pingTimer = null }
    const socket = this.socket
    this.socket = null
    this.opened = false
    if (!socket) return
    socket.onopen = socket.onmessage = socket.onclose = socket.onerror = null
    if (code !== undefined) { try { socket.close(code) } catch { /* already closed */ } }
  }

  private retry(): void {
    if (this.current.closed || this.retryTimer !== null) return
    const wait = RECONNECT_DELAYS_MS[Math.min(this.attempt, RECONNECT_DELAYS_MS.length - 1)]!
    this.attempt++
    this.update({ status: 'reconnecting' })
    this.retryTimer = setTimeout(() => { this.retryTimer = null; this.open() }, wait)
  }

  private finish(reason: string, code?: number): void {
    if (this.current.closed) return
    if (this.retryTimer !== null) { clearTimeout(this.retryTimer); this.retryTimer = null }
    this.drop(code)
    this.update({ status: 'closed', closed: reason, waiting: null })
  }

  private ping(): void { this.send({ type: 'ping', t: this.now() }) }

  private receive(data: unknown): void {
    let message: Record<string, unknown>
    try { message = JSON.parse(String(data)) as Record<string, unknown> } catch { return }
    if (!message || typeof message !== 'object') return
    // The server answered, so this connection works: the next drop starts the backoff over.
    this.attempt = 0
    switch (message.type) {
      case 'pong':
        if (typeof message.t === 'number' && typeof message.server_ms === 'number') this.clock.sample(message.t, message.server_ms, this.now())
        break
      case 'state': {
        if (typeof message.playing !== 'boolean' || typeof message.as_of_ms !== 'number') break
        const previous = this.current.state
        const state: RoomStateEvent = {
          playing: message.playing, position_ms: typeof message.position_ms === 'number' ? message.position_ms : null, as_of_ms: message.as_of_ms,
          by: typeof message.by === 'string' ? message.by : null, controls_locked: !!message.controls_locked,
          stream_epoch: typeof message.stream_epoch === 'number' ? message.stream_epoch : null,
          stream_start_ms: typeof message.stream_start_ms === 'number' ? message.stream_start_ms : null,
        }
        // Any state clears a waiting notice.
        this.update({ state, controlsLocked: state.controls_locked, waiting: null })
        this.onState?.(state, previous)
        break
      }
      case 'members':
        if (Array.isArray(message.members)) this.update({ members: message.members as RoomMember[] })
        break
      case 'waiting':
        if (typeof message.name === 'string') this.update({ waiting: message.name })
        break
      case 'error':
        this.onError?.(String(message.code ?? 'error'), String(message.message ?? ''))
        break
      case 'closed':
        // The last message before the server closes the socket; the close frame that follows ends it.
        this.closedReason = String(message.reason ?? 'closed')
        break
    }
  }
}
