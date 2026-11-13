import type { AudioTrack } from '../api/types'
// The explicit extension lets the node tests import this module without a bundler.
import { languageName } from '../lib/util.ts'

const CODECS: Record<string, string> = {
  ac3: 'AC-3', eac3: 'E-AC-3', aac: 'AAC', mp3: 'MP3', mp2: 'MP2', dts: 'DTS', truehd: 'TrueHD', flac: 'FLAC', opus: 'Opus', vorbis: 'Vorbis',
}
const LAYOUTS: Record<number, string> = { 1: 'Mono', 2: 'Stereo', 6: '5.1', 7: '6.1', 8: '7.1' }

/** "English · 5.1 · AC-3": what a viewer needs to tell two tracks of one file apart. */
export function trackLabel(track: AudioTrack): string {
  const language = !track.language || track.language === 'und' ? 'Unknown language' : languageName(track.language)
  const layout = track.channels ? LAYOUTS[track.channels] ?? `${track.channels} ch` : null
  const codec = track.codec ? CODECS[track.codec] ?? track.codec.toUpperCase() : null
  return [language, layout, codec].filter(Boolean).join(' · ')
}

/** The track that plays when the viewer picks none: preferred language, else the file's default, else the first. */
export function initialTrack(tracks: readonly AudioTrack[], preferred?: number | null): number | null {
  if (!tracks.length) return null
  if (preferred != null && tracks.some((t) => t.index === preferred)) return preferred
  return (tracks.find((t) => t.default) ?? tracks[0]!).index
}
