import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api, ok } from '../api/client'
import { Button, Chip, Modal } from '../ds'
import { fmtDuration } from '../lib/util'
import { useUi } from '../store/ui'

const reasons = { wrong_programme: 'Wrong programme', missing_start: 'Missing beginning', missing_end: 'Missing ending', picture: 'Picture issue', audio: 'Audio issue' } as const
type Category = keyof typeof reasons

export function RecordingIssueDialog({ id, positionMs, onClose }: { id: string; positionMs: number; onClose: () => void }) {
  const [category, setCategory] = useState<Category>('wrong_programme')
  const toast = useUi(s => s.toast)
  const send = useMutation({
    mutationFn: () => ok(api.POST('/recordings/{id}/issues', { params: { path: { id } }, body: { category, position_ms: positionMs } })),
    onSuccess: () => { toast(`Recording report received at ${fmtDuration(positionMs / 1000)}.`, 'success'); onClose() },
  })
  return <Modal open onClose={onClose} title="Report a recording issue" firstFocusKey="issue:wrong_programme" variant="sheet" testId="recording-issue">
    <p>Playback position · {fmtDuration(positionMs / 1000)}</p>
    <div className="lib-chips">{(Object.keys(reasons) as Category[]).map(key =>
      <Chip key={key} focusKey={`issue:${key}`} pressed={category === key} onEnter={() => setCategory(key)}>{reasons[key]}</Chip>)}</div>
    {send.error && <p role="alert">The report could not be sent. Try again.</p>}
    <div className="lib-chips"><Button focusKey="issue:submit" primary disabled={send.isPending} onEnter={() => send.mutate()}>Send report</Button>
      <Button focusKey="issue:close" onEnter={onClose}>Cancel</Button></div>
  </Modal>
}
