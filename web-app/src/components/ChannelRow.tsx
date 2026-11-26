import { Focusable, type FocusableProps } from '../nav/Focusable'
import { Icon } from '../ds'
import { LogoBlock, ProgressBar } from './ui'
import { fmtTime, progressOf, useNow } from '../lib/util'
import type { Channel } from '../api/types'
import { RecordingBadge } from '../recordings/RecordingBadge'
import { recordingStateLabel } from '../lib/recordings'

export function ChannelRow(p: {
  channel: Channel; focusKey: string; onEnter: () => void; onFocus?: FocusableProps['onFocus']; compact?: boolean
  /** A long list (Live TV's Channels): one line of what is on, what is next left to the details beside the list. */
  dense?: boolean
  /** For a list that places its rows and moves focus itself (Live TV's Channels): see Focusable. */
  style?: FocusableProps['style']; data?: FocusableProps['data']; onArrow?: FocusableProps['onArrow']; scroll?: FocusableProps['scroll']
}) {
  const c = p.channel
  const now = useNow()
  const pct = c.now ? progressOf(c.now.start, c.now.stop, now) : null
  return (
    <Focusable focusKey={p.focusKey} className={p.dense ? 'chan-row chan-row-dense fx-flat' : 'chan-row fx-flat'} role="option" data-testid="channel-row" label={`${c.number} ${c.name}${c.now ? `, now ${c.now.title}` : ''}${recordingStateLabel(c.now?.recording_state) ? `, ${recordingStateLabel(c.now?.recording_state)}` : ''}`}
      onEnter={p.onEnter} onFocus={p.onFocus} onArrow={p.onArrow} scroll={p.scroll ?? 'nearest'} style={p.style} data={p.data}>
      <span className="chan-row-num">{c.number}</span>
      <LogoBlock name={c.name} logo={c.logo} />
      <span className="chan-row-copy">
        <span className="chan-row-line">
          <span className="chan-row-name truncate">{c.name}</span>
          {c.is_favorite && <span className="chan-row-fav"><Icon name="heart" size={14} filled /></span>}
        </span>
        {!p.compact && (
          <>
            <span className="chan-row-line"><span className="label truncate">{c.now ? `${c.now.title}` : 'No programme data'}</span><RecordingBadge state={c.now?.recording_state} short /></span>
            {!p.dense && c.next && <span className="chan-row-line"><span className="label truncate chan-row-next">Next · {fmtTime(c.next.start)} {c.next.title}</span><RecordingBadge state={c.next.recording_state} short /></span>}
            {pct !== null && (p.dense ? <span className="chan-row-progress"><ProgressBar label={`Programme progress · ${c.now?.title ?? c.name}`} pct={pct} /></span> : <ProgressBar label={`Programme progress · ${c.now?.title ?? c.name}`} pct={pct} className="mt-1" />)}
          </>
        )}
      </span>
    </Focusable>
  )
}
