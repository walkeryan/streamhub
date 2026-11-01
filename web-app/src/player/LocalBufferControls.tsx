import { useState } from 'react'
import { Button } from '../components/ui'
import { Focusable, FocusGroup } from '../nav/Focusable'
import type { BufferWindow } from './bufferSession'

/** UTC positions refer only to the retained, sealed window advertised by the server. */
export function LocalBufferControls({ window: value, issue, onSeek }: { window: BufferWindow; issue: string | null; onSeek: (at: string | null) => unknown }) {
  const [selected, setSelected] = useState<number | null>(null)
  const start = value.start ? Date.parse(value.start) : 0
  const end = value.end ? Date.parse(value.end) : 0
  const available = end > start
  const position = Math.max(start, Math.min(selected ?? end - 1000, end - 1000))
  const clock = (ms: number) => new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  const move = (seconds: number) => setSelected(Math.max(start, Math.min(position + seconds * 1000, end - 1000)))
  return <FocusGroup focusKey="buffer:controls" data-testid="local-buffer-controls" className="flex flex-wrap items-center gap-3" label="Local live buffer">
    <span className="text-sm">{available ? `${Math.floor(value.buffered_seconds / 60)}m ${Math.floor(value.buffered_seconds % 60)}s buffered · ${clock(start)}–${clock(end)}` : 'Building buffer — no earlier history yet'}</span>
    {available && <>
      <Button focusKey="buffer:earlier" onEnter={() => move(-10)}>10 s earlier</Button>
      <Focusable focusKey="buffer:position" label="Buffered playback position. Left and Right adjust, Select plays." className="chip"
        onArrow={(direction) => { if (direction === 'left' || direction === 'right') { move(direction === 'left' ? -10 : 10); return false } return true }}
        onEnter={() => void onSeek(new Date(position).toISOString())}>Play {clock(position)}</Focusable>
      <Button focusKey="buffer:later" onEnter={() => move(10)}>10 s later</Button>
    </>}
    <Button focusKey="buffer:live" onEnter={() => { setSelected(null); void onSeek(null) }}>Go Live</Button>
    {issue && <span role="alert" className="text-sm">{issue}</span>}
  </FocusGroup>
}
