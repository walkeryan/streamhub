import { Focusable } from '../nav/Focusable'
import { Icon } from './Icon'
import { LogoBlock, ProgressBar } from './ui'
import { fmtTime, progressOf, useNow } from '../lib/util'
import type { Channel } from '../api/types'
import { RecordingBadge } from '../recordings/RecordingBadge'
import { recordingStateLabel } from '../lib/recordings'

export function ChannelRow(p: { channel: Channel; focusKey: string; onEnter: () => void; onFocus?: () => void; compact?: boolean }) {
  const c = p.channel
  const now = useNow()
  const pct = c.now ? progressOf(c.now.start, c.now.stop, now) : null
  return (
    <Focusable focusKey={p.focusKey} className="chan-row fx-flat" role="option" data-testid="channel-row" label={`${c.number} ${c.name}${c.now ? `, now ${c.now.title}` : ''}${recordingStateLabel(c.now?.recording_state) ? `, ${recordingStateLabel(c.now?.recording_state)}` : ''}`}
      onEnter={p.onEnter} onFocus={p.onFocus} scroll="nearest">
      <span className="heading" style={{ color: 'var(--t-text-muted)', textAlign: 'right' }}>{c.number}</span>
      <LogoBlock name={c.name} logo={c.logo} />
      <span className="flex flex-col min-w-0 gap-0.5">
        <span className="flex items-center gap-2 min-w-0">
          <span className="truncate" style={{ fontWeight: 600 }}>{c.name}</span>
          {c.is_favorite && <span style={{ color: 'var(--t-accent)' }}><Icon name="heart" size={14} filled /></span>}
        </span>
        {!p.compact && (
          <>
            <span className="flex items-center gap-2 min-w-0"><span className="label truncate">{c.now ? `${c.now.title}` : 'No programme data'}</span><RecordingBadge state={c.now?.recording_state} short /></span>
            {c.next && <span className="flex items-center gap-2 min-w-0"><span className="label truncate" style={{ opacity: 0.8 }}>Next · {fmtTime(c.next.start)} {c.next.title}</span><RecordingBadge state={c.next.recording_state} short /></span>}
            {pct !== null && <ProgressBar pct={pct} className="mt-1" />}
          </>
        )}
      </span>
    </Focusable>
  )
}
