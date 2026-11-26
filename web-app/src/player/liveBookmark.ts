const key = (profile: string, channel: string) => `sh.live.place.${profile}.${channel}`
export function liveBookmark(profile: string, channel: string, start: string) {
  try {
    const saved = JSON.parse(localStorage.getItem(key(profile, channel)) ?? 'null')
    return saved?.start === start && typeof saved.at === 'string' && Number.isFinite(Date.parse(saved.at)) ? saved.at as string : undefined
  } catch { return undefined }
}
export function saveLiveBookmark(profile: string, channel: string, start: string, at: number) {
  try { localStorage.setItem(key(profile, channel), JSON.stringify({ start, at: new Date(at).toISOString() })) } catch { /* Private browsers may disable storage. */ }
}
