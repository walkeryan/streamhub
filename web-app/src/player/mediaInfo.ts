import type { components } from '../api/schema'
// The explicit extensions let the node tests import this module without a bundler.
import { CODECS as AUDIO_CODECS, LAYOUTS } from './audioTracks.ts'
import { LANG_NAMES, fmtDuration } from '../lib/util.ts'

export type MediaInfo = components['schemas']['MediaInfo']

/** What the player itself measures, once a second, while the panel is open. */
export interface LiveNumbers {
  /** Seconds of media downloaded ahead of the playhead. */
  bufferedAheadS: number | null
  droppedFrames: number | null
  totalFrames: number | null
  /** The engine's own estimate of the download rate, when it makes one. */
  downloadKbps: number | null
  /** Seconds into the title. */
  positionS: number
  /** Where in the title this stream began (restart-at-offset streams). */
  streamStartS: number | null
  /** The picture the browser is decoding. */
  decodedWidth: number
  decodedHeight: number
  speed: number
}

const CONTAINERS: Record<string, string> = {
  matroska: 'MKV', mov: 'MP4', mp4: 'MP4', mpegts: 'MPEG-TS', avi: 'AVI', webm: 'WebM', flv: 'FLV', asf: 'WMV', mpeg: 'MPEG', ogg: 'Ogg',
}
const VIDEO_CODECS: Record<string, string> = {
  h264: 'H.264', hevc: 'HEVC (H.265)', av1: 'AV1', vp9: 'VP9', vp8: 'VP8', mpeg2video: 'MPEG-2', mpeg4: 'MPEG-4', vc1: 'VC-1', wmv3: 'WMV', theora: 'Theora',
}
const RANGES: Record<string, string> = { sdr: 'SDR', hdr10: 'HDR10', hlg: 'HLG' }

export const containerLabel = (name: string): string => CONTAINERS[name] ?? name.toUpperCase()
export const videoCodecLabel = (name: string): string => VIDEO_CODECS[name] ?? name.toUpperCase()

/** "18.3 Mbit/s", "850 kbit/s". */
export function rateLabel(kbps: number): string {
  return kbps >= 1000 ? `${(kbps / 1000).toFixed(kbps >= 10_000 ? 0 : 1)} Mbit/s` : `${Math.round(kbps)} kbit/s`
}

/** "3840 × 1600": the size alone, since a wide film is not a "2160p" one. */
export const sizeLabel = (width: number, height: number): string => `${width} × ${height}`

/** "23.976 fps", "25 fps". */
export const frameRateLabel = (fps: number): string => `${Number(fps.toFixed(3))} fps`

/** How the stream reaches this player, in a sentence a viewer can act on. */
export function deliveryLabel(info: Pick<MediaInfo, 'delivery' | 'encoder'>): string {
  if (info.delivery === 'direct') return 'Direct: the file as it is'
  if (info.delivery === 'remux') return 'Remux: video copied, not re-encoded'
  return `Transcode: video re-encoded${info.encoder === 'hardware' ? ' by hardware' : info.encoder === 'software' ? ' in software' : ''}`
}

export interface InfoSection { title: string; rows: Array<[label: string, value: string]> }

/** The panel's rows. Only what is known gets a row: an empty section is left out altogether. */
export function infoSections(info: MediaInfo | null | undefined, live: LiveNumbers | null): InfoSection[] {
  const sections: InfoSection[] = []
  const add = (title: string, rows: Array<[string, string | null | undefined | false]>) => {
    const known = rows.filter((row): row is [string, string] => typeof row[1] === 'string' && row[1] !== '')
    if (known.length) sections.push({ title, rows: known })
  }
  if (info) {
    const v = info.video, a = info.audio
    add('Source', [
      ['From', info.source_name],
      ['Container', info.container && containerLabel(info.container)],
      ['Bit rate', info.bitrate_kbps != null && rateLabel(info.bitrate_kbps)],
    ])
    add('Video', [
      ['Codec', v?.codec && videoCodecLabel(v.codec)],
      ['Resolution', v?.width && v.height ? sizeLabel(v.width, v.height) : null],
      ['Frame rate', v?.frame_rate != null && frameRateLabel(v.frame_rate)],
      ['Range', v?.dynamic_range && RANGES[v.dynamic_range]],
    ])
    add('Audio', [
      ['Codec', a?.codec && (AUDIO_CODECS[a.codec] ?? a.codec.toUpperCase())],
      ['Channels', a?.channels != null && (LAYOUTS[a.channels] ?? `${a.channels} ch`)],
      ['Language', a?.language && (LANG_NAMES[a.language] ?? a.language.toUpperCase())],
    ])
    add('Delivery', [
      ['Method', deliveryLabel(info)],
      // A remux or a transcode always carries one track as AAC stereo.
      ['Audio sent as', info.delivery !== 'direct' && a ? 'AAC stereo' : null],
      ['Picture sent', info.output_width && info.output_height ? sizeLabel(info.output_width, info.output_height) : null],
      ['Quality', info.max_height ? `Limited to ${info.max_height}p` : null],
    ])
  }
  if (live) {
    add('Player', [
      ['Position', fmtDuration(live.positionS)],
      ['Stream starts at', live.streamStartS != null && fmtDuration(live.streamStartS)],
      ['Buffered ahead', live.bufferedAheadS != null && `${live.bufferedAheadS.toFixed(1)} s`],
      ['Download', live.downloadKbps != null && rateLabel(live.downloadKbps)],
      ['Dropped frames', live.droppedFrames != null && live.totalFrames != null && `${live.droppedFrames} of ${live.totalFrames}`],
      ['Decoding', live.decodedWidth > 0 && live.decodedHeight > 0 && sizeLabel(live.decodedWidth, live.decodedHeight)],
      ['Speed', live.speed !== 1 && `${live.speed}×`],
    ])
  }
  return sections
}

/** Seconds buffered ahead of `time` in the range that contains it; 0 when the playhead is in none. */
export function bufferedAhead(ranges: ReadonlyArray<readonly [number, number]>, time: number): number {
  for (const [from, to] of ranges) if (time >= from - 0.25 && time <= to) return Math.max(0, to - time)
  return 0
}
