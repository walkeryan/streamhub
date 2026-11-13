import type { RecordingState } from '../api/types'
import { recordingStateLabel } from '../lib/recordings'

/** The state of a programme's recording, in words (never colour alone). `short` fits a guide cell. */
export function RecordingBadge({ state, short }: { state: RecordingState | null | undefined; short?: boolean }) {
  const label = recordingStateLabel(state)
  if (!label || !state) return null
  const text = !short ? label : state === 'scheduled' ? 'Will record' : state === 'recording' ? 'REC' : state === 'done' ? 'Recorded' : state === 'failed' ? 'Failed' : 'Missed'
  return <span className="badge rec-badge" data-state={state} data-testid="recording-badge" title={label}>{(state === 'recording' || state === 'scheduled') && <i aria-hidden="true" />}{text}</span>
}
