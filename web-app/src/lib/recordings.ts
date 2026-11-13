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

const SPORT_CATEGORY = /sport|football|soccer|basketball|baseball|hockey|tennis|golf|rugby|cricket|motor|racing|boxing|wrestling|athletics|cycling/i
const FIXTURE = /^(?:.*?:\s*)?(.+?)\s+(?:vs?\.?|@|at)\s+(.+)$/i

/** Does a guide entry look like a sports event? The guide gives a category and a title, nothing firmer. */
export const looksLikeSport = (p: Pick<Programme, 'title' | 'category'>): boolean => SPORT_CATEGORY.test(p.category ?? '') || FIXTURE.test(p.title)

/** A first guess at the team to follow: the first side of "A vs B", else the title itself. The viewer edits it. */
export function teamFrom(title: string): string {
  const plain = title.replace(/\s*\((?:live|new|repeat)\)\s*$/i, '').trim()
  return (FIXTURE.exec(plain)?.[1] ?? plain).trim()
}

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
