/**
 * Server time for a watch room. The server's clock is the only one members agree on, so each client
 * measures how far its own clock is from it with ping/pong and never trusts another member's.
 *
 * A pong answers a ping sent at `sent` and received back at `received` with the server's time in
 * between: offset = server_ms − (sent + received) / 2. The error of one sample is at most half its
 * round trip, so the sample with the smallest round trip is the one kept.
 */
export interface ClockSample { offsetMs: number; roundTripMs: number }

/** Recent samples only: a local clock that was stepped, or slept, must not pin an old offset for good. */
const WINDOW = 24

export function clockSample(sentMs: number, receivedMs: number, serverMs: number): ClockSample | null {
  const roundTripMs = receivedMs - sentMs
  if (!Number.isFinite(sentMs + receivedMs + serverMs) || roundTripMs < 0) return null
  return { offsetMs: serverMs - (sentMs + receivedMs) / 2, roundTripMs }
}

export class RoomClock {
  private readonly now: () => number
  private samples: ClockSample[] = []
  private best: ClockSample | null = null
  /** A first guess from an HTTP answer (Room.server_ms); any real sample replaces it. */
  private seeded: number | null = null

  constructor(now: () => number = Date.now) { this.now = now }

  /** Record a pong: `sentMs` is the echoed ping time, `serverMs` the server's clock when it answered. */
  sample(sentMs: number, serverMs: number, receivedMs: number = this.now()): ClockSample | null {
    const s = clockSample(sentMs, receivedMs, serverMs)
    if (!s) return null
    this.samples = [...this.samples.slice(-(WINDOW - 1)), s]
    this.best = this.samples.reduce((a, b) => (b.roundTripMs < a.roundTripMs ? b : a))
    return s
  }

  seed(serverMs: number, receivedMs: number = this.now()): void {
    if (Number.isFinite(serverMs)) this.seeded = serverMs - receivedMs
  }

  /** True once a pong has been measured; until then serverNow() is only as good as the seed. */
  get synced(): boolean { return this.best !== null }
  get offsetMs(): number { return this.best?.offsetMs ?? this.seeded ?? 0 }
  get roundTripMs(): number | null { return this.best?.roundTripMs ?? null }
  serverNow(): number { return this.now() + this.offsetMs }
  reset(): void { this.samples = []; this.best = null }
}
