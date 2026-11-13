/**
 * Seeking in a restart-at-offset stream (see usePlayback). The media element's timeline starts at
 * `offsetS` into the title and only covers what the server has produced so far.
 */
export type SeekPlan = { within: number } | { restart: number }

/**
 * Where a seek to `targetS` (seconds into the title) lands: `within` is a media-element time inside
 * a seekable range; `restart` is a title position in milliseconds to start a new stream at.
 */
export function seekPlan(targetS: number, offsetS: number, ranges: ReadonlyArray<readonly [number, number]>, durationS: number): SeekPlan {
  // Never restart on the last second: a stream that starts at the end has nothing to play.
  const target = Math.max(0, durationS > 0 ? Math.min(targetS, Math.max(0, durationS - 1)) : targetS)
  const local = target - offsetS
  for (const [start, end] of ranges) if (local >= start && local <= end) return { within: local }
  return { restart: Math.floor(target * 1000) }
}

export function seekableRanges(video: Pick<HTMLVideoElement, 'seekable'>): Array<[number, number]> {
  const out: Array<[number, number]> = []
  for (let i = 0; i < video.seekable.length; i++) out.push([video.seekable.start(i), video.seekable.end(i)])
  return out
}
