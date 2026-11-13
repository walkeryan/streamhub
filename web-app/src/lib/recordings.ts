import type { Programme, Recording, RecordingRule, RecordingState } from '../api/types'

/** Viewer wording for the state of a recording; a cancelled one is not shown at all. */
export function recordingStateLabel(state: RecordingState | null | undefined): string | null {
  switch (state) {
    case 'scheduled': return 'Will record'
    case 'recording': return 'Recording'
    case 'done': return 'Recorded'
    case 'failed': return 'Recording failed'
    case 'missed': return 'Recording missed'
    default: return null
  }
}

export const RULE_KIND_LABEL: Record<RecordingRule['kind'], string> = { series: 'Every new episode', team: 'Every game', keyword: 'Keyword' }

/** The sides the server read out of a sports title, as one line: "A v B". Null when it named none. */
export const sidesLabel = (teams: readonly string[] | null | undefined): string | null => (teams?.length ? teams.join(' v ') : null)

/** The series a guide entry belongs to: its title without the guide's own markers. */
export const seriesTitle = (title: string): string => title.replace(/\s*\((?:live|new|repeat)\)\s*$/i, '').trim()

/** The recording made of a guide entry: same channel, same listed start (programme ids do not survive a guide refresh). */
export const recordingOf = (recordings: Recording[] | undefined, p: Pick<Programme, 'channel_id' | 'start'>): Recording | undefined =>
  recordings?.find((r) => r.channel_id === p.channel_id && r.state !== 'cancelled' && Date.parse(r.starts_at) === Date.parse(p.start))

export function recordingSize(bytes: number | null | undefined): string | null {
  if (!bytes) return null
  return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1e6))} MB`
}

export const episodeLabel = (r: Pick<Recording, 'season' | 'episode'>): string | null =>
  r.season != null && r.episode != null ? `S${r.season} · E${r.episode}` : r.episode != null ? `Episode ${r.episode}` : null
