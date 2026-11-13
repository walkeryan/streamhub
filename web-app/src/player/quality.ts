// The explicit extension lets the node tests import this module without a bundler.
import { lsGet, lsSet } from '../lib/util.ts'
import type { MediaInfo } from './mediaInfo.ts'

/** The quality caps the server offers (PlaybackRequest.max_height). */
export type QualityHeight = 480 | 720 | 1080
export const QUALITIES: readonly QualityHeight[] = [1080, 720, 480]
/**
 * What the server holds the video of each capped quality to, in kbit/s, with the 160 kbit/s of audio
 * it always sends beside it. The numbers are the contract's; they are here to say what an hour costs.
 */
export const QUALITY_KBPS: Record<QualityHeight, number> = { 1080: 5000, 720: 2800, 480: 1400 }
const AUDIO_KBPS = 160
/** An uncapped transcode is held to this much video. */
const TRANSCODE_KBPS = 8000

export const isQuality = (value: unknown): value is QualityHeight => value === 480 || value === 720 || value === 1080

/** "about 1.3 GB per hour", "about 700 MB per hour": what a stream at this total rate uses. */
export function dataRateText(kbps: number): string {
  const megabytes = (kbps * 3600) / 8 / 1000
  if (megabytes >= 950) return `about ${(megabytes / 1000).toFixed(megabytes >= 9500 ? 0 : 1)} GB per hour`
  return `about ${Math.max(50, Math.round(megabytes / 50) * 50)} MB per hour`
}

export interface QualityOption {
  /** null is the file as the server would send it without a cap. */
  height: QualityHeight | null
  label: string
  /** What an hour costs, when it can be said. */
  rate: string | null
}

/**
 * The choices for a title: Original, and each cap that is below the source's height. A cap the
 * source already meets would change nothing on the server, so it is not offered; a source of
 * unknown height gets Original alone.
 */
export function qualityOptions(info: MediaInfo | null | undefined): QualityOption[] {
  const height = info?.video?.height ?? null
  let original: number | null = info?.bitrate_kbps ?? null
  // Without a cap a transcode is the file's rate at most, and never more than its own ceiling.
  if (original !== null && info?.delivery === 'transcode' && !info.max_height) original = Math.min(original, TRANSCODE_KBPS + AUDIO_KBPS)
  const options: QualityOption[] = [{ height: null, label: height ? `Original (${height}p)` : 'Original', rate: original ? dataRateText(original) : null }]
  if (height) for (const cap of QUALITIES) if (cap < height) options.push({ height: cap, label: `${cap}p`, rate: dataRateText(QUALITY_KBPS[cap] + AUDIO_KBPS) })
  return options
}

/** The stored choice if this title offers it, else Original. */
export function chosenQuality(options: readonly QualityOption[], wanted: QualityHeight | null): QualityHeight | null {
  return options.some((option) => option.height === wanted) ? wanted : null
}

interface Connection { saveData?: boolean; type?: string; effectiveType?: string }
/** The browser says data is short: the Save-Data preference, a cellular link, or one it rates as slow. */
export function savingData(connection: Connection | null | undefined): boolean {
  if (!connection) return false
  return connection.saveData === true || connection.type === 'cellular' || /^(slow-2g|2g|3g)$/.test(connection.effectiveType ?? '')
}

const KEY = 'sh.player.quality.v1'
export interface StoredQuality { normal: QualityHeight | null; saver: QualityHeight | null }
export function parseQuality(raw: string | null): StoredQuality {
  let found: Partial<Record<keyof StoredQuality, unknown>> = {}
  try { const parsed: unknown = raw ? JSON.parse(raw) : null; if (parsed && typeof parsed === 'object') found = parsed } catch { /* an unreadable value is no value */ }
  return { normal: isQuality(found.normal) ? found.normal : null, saver: isQuality(found.saver) ? found.saver : null }
}
/** One choice for ordinary connections and one for when data is short, each remembered by this browser. */
export const loadQuality = (saver: boolean): QualityHeight | null => parseQuality(lsGet(KEY))[saver ? 'saver' : 'normal']
export function saveQuality(saver: boolean, height: QualityHeight | null): void {
  lsSet(KEY, JSON.stringify({ ...parseQuality(lsGet(KEY)), [saver ? 'saver' : 'normal']: height }))
}
