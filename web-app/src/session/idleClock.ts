/** The earlier heartbeat is also the start of idle time when playback stops or the device sleeps. */
export function idleExpired(activityAt: number, minutes: number, now: number): boolean {
  return (
    Number.isFinite(activityAt) && Number.isInteger(minutes) && minutes > 0 && now - activityAt >= minutes * 60_000
  )
}
export function idleSessionId(access: string | null): string | null {
  try {
    const part = access?.split('.')[1]
    if (!part) return null
    const sid = JSON.parse(atob(part.replace(/-/g, '+').replace(/_/g, '/'))).sid
    return typeof sid === 'string' && /^[a-zA-Z0-9_-]{1,64}$/.test(sid) ? sid : null
  } catch {
    return null
  }
}
