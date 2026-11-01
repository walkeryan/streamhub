import { api, API_BASE, ApiError, unwrap } from '../api/client'
import type { components } from '../api/schema'
import type { PlaybackDescriptor } from '../api/types'
import { useSession } from '../store/session'

export type BufferWindow = components['schemas']['BufferWindow']
type LeaseResponse = components['schemas']['BufferLeaseResponse']
type SeekResponse = components['schemas']['BufferSeekResponse']

/** One resolved playback intent owns one lease. It never acquires another lease on heartbeat/seek. */
export class BufferSession {
  private leaseId: string | null = null
  private closed = false
  private timer: number | null = null
  private requests = new AbortController()
  private seekRequest: AbortController | null = null
  private seekSequence = 0
  private heartbeatPending = false
  private access: string
  private releasePromise: Promise<void> | null = null
  window: BufferWindow | null = null

  private constructor(private profileId: string, private streamToken: string) {
    this.access = useSession.getState().access ?? ''
  }

  static async acquire(descriptor: PlaybackDescriptor, signal: AbortSignal): Promise<BufferSession> {
    const profile = useSession.getState().profile?.id
    const url = new URL(descriptor.url ?? '', window.location.origin)
    const match = /^\/api\/v1\/stream\/([^/]+)\/index\.m3u8$/.exec(url.pathname)
    if (!profile || !match?.[1] || url.origin !== window.location.origin || !descriptor.live || descriptor.mode !== 'hls') {
      throw new Error('This playback does not support a local buffer.')
    }
    const session = new BufferSession(profile, decodeURIComponent(match[1]))
    const abort = () => session.requests.abort()
    signal.addEventListener('abort', abort, { once: true })
    try {
      signal.throwIfAborted()
      const lease = await unwrap<LeaseResponse>(api.POST('/playback/buffer/lease', {
        signal: session.requests.signal, body: { stream_token: session.streamToken },
      }))
      session.leaseId = lease.lease_id
      session.window = lease.window
      session.ensureOwner()
      signal.throwIfAborted()
      return session
    } catch (error) {
      await session.release()
      throw error
    } finally {
      signal.removeEventListener('abort', abort)
    }
  }

  private ensureOwner() {
    if (this.closed || useSession.getState().profile?.id !== this.profileId) throw new DOMException('Playback changed.', 'AbortError')
    this.access = useSession.getState().access ?? this.access
  }
  private body() {
    this.ensureOwner()
    if (!this.leaseId) throw new Error('No buffer lease is active.')
    return { stream_token: this.streamToken, lease_id: this.leaseId }
  }

  startHeartbeat(paused: () => boolean, onWindow: (value: BufferWindow) => void, onFailure: (error: unknown) => void) {
    const heartbeat = async () => {
      if (this.closed || this.heartbeatPending) return
      this.heartbeatPending = true
      try {
        const value = await unwrap<BufferWindow>(api.POST('/playback/buffer/heartbeat', {
          signal: this.requests.signal, body: { ...this.body(), paused: paused() },
        }))
        this.ensureOwner()
        this.window = value
        onWindow(value)
      } catch (error) {
        if (!this.closed) { onFailure(error); void this.release() }
      } finally { this.heartbeatPending = false }
    }
    this.timer = window.setInterval(() => void heartbeat(), 30_000)
    return () => { void heartbeat() }
  }

  async seek(at: string | null): Promise<SeekResponse> {
    const body = this.body()
    this.seekRequest?.abort()
    const controller = new AbortController()
    this.seekRequest = controller
    const sequence = ++this.seekSequence
    const result = await unwrap<SeekResponse>(api.POST('/playback/buffer/seek', { signal: controller.signal, body: { ...body, paused: false, at } }))
    this.ensureOwner()
    if (sequence !== this.seekSequence) throw new DOMException('Newer seek selected.', 'AbortError')
    this.window = result.window
    return result
  }

  release(): Promise<void> {
    if (this.releasePromise) return this.releasePromise
    this.closed = true
    if (this.timer !== null) window.clearInterval(this.timer)
    this.requests.abort()
    this.seekRequest?.abort()
    // A profile switch must not send the old viewer lease under the new profile.
    // Use this intent's last known bearer solely for bounded best-effort cleanup.
    const lease = this.leaseId
    this.leaseId = null
    this.releasePromise = lease ? fetch(`${API_BASE}/playback/buffer/release`, {
      method: 'POST', keepalive: true, signal: AbortSignal.timeout(3000),
      headers: { 'content-type': 'application/json', authorization: `Bearer ${this.access}` },
      body: JSON.stringify({ stream_token: this.streamToken, lease_id: lease }),
    }).then(() => undefined, () => undefined) : Promise.resolve()
    return this.releasePromise
  }
}

export function bufferError(error: unknown): string {
  if (error instanceof ApiError && error.code === 'buffer_expired') return 'That buffered position expired. Choose a newer position or Go Live.'
  return error instanceof Error ? error.message : 'Local buffer unavailable.'
}
