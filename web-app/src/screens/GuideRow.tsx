import { memo } from 'react'
import type { Channel, Programme } from '../api/types'
import { LogoBlock } from '../components/ui'
import { Focusable } from '../nav/Focusable'
import { fmtTime } from '../lib/util'
import { cellBox, type TimeSpan } from '../lib/guideWindow'
import { recordingStateLabel } from '../lib/recordings'
import { RecordingBadge } from '../recordings/RecordingBadge'

export const COL_REM = 13 // 30-minute column width, see .epg --col
export const REM_PER_MIN = COL_REM / 30
export const ROW_REM = 3 // see .epg --epg-row
export const channelKey = (channelId: string) => `guide:ch:${channelId}`
export const cellKey = (channelId: string, programmeId: string) => `epg::${channelId}::${programmeId}`

/**
 * One channel of the guide. Rows are drawn only near the view, at a fixed height, so each is placed by
 * its index. Memoised with stable handlers: scrolling re-renders the rows that enter, not the ones on screen.
 * Cells do not scroll themselves into view (scroll={false}): the guide does that for the keyboard only,
 * or resting the pointer on a half-shown cell would move the grid under it.
 */
export const GuideRow = memo(function GuideRow({ index, channel: c, programmes, loading, span, now, tabKey, onFocusCell, onMove, onOpen, onPlay }: {
  index: number; channel: Channel; programmes: readonly Programme[]; loading: boolean; span: TimeSpan; now: number
  /** The cell of this row that Tab stops on, when the guide's one tab stop is in it. */
  tabKey: string | null
  onFocusCell: (channel: Channel, programme: Programme | null, node: HTMLElement) => void
  /** An arrow pressed on a cell (a null id: the channel cell). False once the guide has moved focus itself. */
  onMove: (row: number, programmeId: string | null, direction: string) => boolean
  onOpen: (channel: Channel, programme: Programme) => void
  onPlay: (channel: Channel) => void
}) {
  const cells = programmes.map((p) => {
    const box = cellBox(p, span)
    if (!box) return null
    const isNow = Date.parse(p.start) <= now && Date.parse(p.stop) > now
    const past = Date.parse(p.stop) <= now
    const key = cellKey(c.id, p.id), state = recordingStateLabel(p.recording_state)
    return (
      <Focusable key={p.id} focusKey={key} className="epg-cell fx-flat" scroll={false} label={`${c.name}: ${p.title}, ${fmtTime(p.start)}${state ? `, ${state}` : ''}`}
        data={{ now: isNow, past, 'tab-group': 'guide', 'tab-current': tabKey === key }}
        onEnter={() => onOpen(c, p)} onFocus={(layout) => onFocusCell(c, p, layout.node)} onArrow={(d) => onMove(index, p.id, d)}
        style={{ left: `${box.left * REM_PER_MIN}rem`, width: `calc(${box.width * REM_PER_MIN}rem - 0.25rem)` }}>
        <span className="epg-label">
          <span className="truncate" style={{ fontWeight: 600 }}>{p.title}</span>
          <span className="flex items-center gap-2 min-w-0"><span className="label truncate">{fmtTime(p.start)}{p.catchup_available && past ? ' · Catch-up' : ''}{isNow ? ' · On now' : ''}</span><RecordingBadge state={p.recording_state} short /></span>
        </span>
      </Focusable>
    )
  })
  return (
    <div className="epg-row" style={{ top: `${index * ROW_REM}rem` }} data-testid="guide-row" data-row={index}>
      <div className="epg-chan">
        <Focusable focusKey={channelKey(c.id)} className="fx-flat" scroll={false} label={`${c.number} ${c.name}`}
          data={{ 'tab-group': 'guide', 'tab-current': tabKey === channelKey(c.id) }}
          onFocus={(layout) => onFocusCell(c, null, layout.node)} onArrow={(d) => onMove(index, null, d)} onEnter={() => onPlay(c)}>
          <span className="label epg-num">{c.number}</span>
          <LogoBlock name={c.name} logo={c.logo} />
          <span className="epg-name">{c.name}</span>
        </Focusable>
      </div>
      <div className="epg-track">
        {!cells.some(Boolean) && <span className="label muted epg-none">{loading ? 'Loading schedule…' : c.epg_id ? 'No schedule supplied for this time' : 'No guide mapping supplied'}</span>}
        {cells}
      </div>
    </div>
  )
})
