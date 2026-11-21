/** The hub puts 24 JPEG tiles and their offset from the first audio sample into each two-second
 * HLS segment. Native HLS and hls.js expose the private ID3 frame on the audio metadata track. */
interface PictureCue extends TextTrackCue {
  value?: { key?: string; info?: string; data?: ArrayBuffer }
}

type Sheet = { startTime: number; bytes: Uint8Array<ArrayBuffer> }
const timelines = new WeakMap<HTMLMediaElement, Map<number, Sheet>>()

/** Keep metadata on the same timeline as the media buffer. Some MSE implementations
 * discard the first in-band TextTrack cue when they initialize the audio SourceBuffer. */
export function startCanvasTimeline(media: HTMLMediaElement) {
  const sheets = new Map<number, Sheet>()
  timelines.set(media, sheets)
  const owner = new TextEncoder().encode('streamhub.canvas.v1\0')
  return {
    add(startTime: number, tag: Uint8Array) {
      const header = new DataView(tag.buffer, tag.byteOffset, tag.byteLength)
      for (let i = 10; i + 10 <= tag.length;) {
        const size = (header.getUint8(i + 4) << 21) | (header.getUint8(i + 5) << 14) | (header.getUint8(i + 6) << 7) | header.getUint8(i + 7)
        const end = i + 10 + size
        if (!size || end > tag.length) break
        if (header.getUint32(i) === 0x50524956 /* PRIV */ &&
          size > owner.length + 4 && owner.every((byte, k) => tag[i + 10 + k] === byte)) {
          const bytes = tag.slice(i + 10 + owner.length, end)
          sheets.set(startTime, { startTime, bytes })
          // Bound memory even on engines that never report a back-buffer flush.
          if (sheets.size > 40) sheets.delete(sheets.keys().next().value!)
          break
        }
        i = end
      }
    },
    flush(start: number, end: number) {
      for (const time of sheets.keys()) if (time >= start && time + 2 <= end) sheets.delete(time)
    },
    stop() { if (timelines.get(media) === sheets) timelines.delete(media); sheets.clear() },
  }
}

// SD has one 4×6 JPEG after the timing offset. HD stores a count and length-prefixed 2×3 JPEGs.
function unpackSheets(bytes: Uint8Array<ArrayBuffer>) {
  const count = bytes[4] ?? 0
  if (count === 0xff) return [bytes.subarray(4)]
  const sheets = []
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  let pos = 5
  if (count < 1 || count > 4) return []
  for (let i = 0; i < count; i++) {
    if (pos + 4 > bytes.length) return []
    const length = view.getUint32(pos)
    pos += 4
    if (!length || pos + length > bytes.length) return []
    sheets.push(bytes.subarray(pos, pos + length))
    pos += length
  }
  return pos === bytes.length ? sheets : []
}

export function paintServerFrames(media: HTMLMediaElement, canvas: HTMLCanvasElement): () => void {
  const ctx = canvas.getContext('2d', { alpha: false })
  if (!ctx) return () => {}
  const listeners = new AbortController()
  const options = { signal: listeners.signal }
  let current: Sheet | PictureCue | null = null
  let image: HTMLImageElement | null = null
  let url = ''
  let offset = 0
  let lastFrame = -1
  let timer = 0
  let sheets: Uint8Array<ArrayBuffer>[] = []
  let columns = 4, rows = 6
  let loaded = -1

  function clear() {
    if (url) URL.revokeObjectURL(url)
    current = image = null
    url = ''
    sheets = []
    loaded = -1
    canvas.hidden = true
    lastFrame = -1
  }
  function draw() {
    if (!current || !sheets.length || media.seeking) return
    const perSheet = columns * rows
    const frame = Math.min(sheets.length * perSheet - 1, Math.max(0, Math.floor((media.currentTime - current.startTime - offset) * 12)))
    const index = Math.floor(frame / perSheet)
    if (index !== loaded) {
      loaded = index
      if (url) URL.revokeObjectURL(url)
      const sheet = new Image()
      image = sheet
      sheet.onload = () => { if (sheet === image) draw() }
      url = URL.createObjectURL(new Blob([sheets[index]!], { type: 'image/jpeg' }))
      sheet.src = url
    }
    if (!image?.naturalWidth || frame === lastFrame) return
    const w = image.naturalWidth / columns, h = image.naturalHeight / rows
    if (canvas.width !== w) canvas.width = w
    if (canvas.height !== h) canvas.height = h
    ctx!.drawImage(image, (frame % columns) * w, Math.floor((frame % perSheet) / columns) * h, w, h, 0, 0, w, h)
    canvas.hidden = false
    lastFrame = frame
  }
  function refresh() {
    let active: Sheet | PictureCue | null = null
    const timeline = timelines.get(media)
    if (timeline) {
      for (const sheet of timeline.values()) {
        if (sheet.startTime <= media.currentTime + 0.001 && media.currentTime < sheet.startTime + 2.12 &&
            (!active || active.startTime < sheet.startTime)) active = sheet
      }
    }
    for (const track of timeline ? [] : Array.from(media.textTracks)) {
      if (track.kind !== 'metadata') continue
      if (track.mode === 'disabled') track.mode = 'hidden'
      for (const cue of Array.from(track.activeCues ?? []) as PictureCue[]) {
        if (cue.value?.key === 'PRIV' && cue.value.info === 'streamhub.canvas.v1' && (!active || active.startTime < cue.startTime)) active = cue
      }
    }
    if (active !== current) {
      clear()
      const bytes = active && ('bytes' in active ? active.bytes : active.value?.data && new Uint8Array(active.value.data))
      if (active && bytes && bytes.byteLength > 4) {
        current = active
        offset = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getInt32(0) / 1000
        sheets = unpackSheets(bytes)
        columns = bytes[4] === 0xff ? 4 : 2
        rows = bytes[4] === 0xff ? 6 : 3
      }
    }
    draw()
  }
  function tick() {
    cancelAnimationFrame(timer)
    refresh()
    if (!media.paused && !media.ended && !document.hidden) timer = requestAnimationFrame(tick)
  }
  for (const event of ['playing', 'pause', 'seeked', 'timeupdate', 'loadedmetadata', 'ended']) media.addEventListener(event, tick, options)
  for (const event of ['emptied', 'loadstart', 'seeking', 'error']) media.addEventListener(event, clear, options)
  media.textTracks.addEventListener('addtrack', tick, options)
  document.addEventListener('visibilitychange', tick, options)
  tick()
  return () => { listeners.abort(); cancelAnimationFrame(timer); clear() }
}
