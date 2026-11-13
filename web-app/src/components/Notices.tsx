import { useQueryClient } from '@tanstack/react-query'
import { useNotices, useReadNotice } from '../api/hooks'
import type { Notice } from '../api/types'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { useGo } from '../nav/back'
import { setFocus } from '../nav/spatial'
import { Button } from './ui'

const SHOWN = 3

/** Unread notices as a banner row on Home: Enter opens the title, Dismiss marks it read. */
export function HomeNotices() {
  const { data } = useNotices()
  const read = useReadNotice()
  const go = useGo()
  const qc = useQueryClient()
  const unread = (data ?? []).filter((n) => !n.read_at).slice(0, SHOWN)
  if (!unread.length) return null
  const open = (n: Notice) => {
    read.mutate(n.id)
    // A notice means the title changed since it was last loaded; do not show the cached page.
    void qc.invalidateQueries({ queryKey: ['title'] })
    if (n.ref_id) go(`/title/${encodeURIComponent(n.ref_id)}`)
  }
  const dismiss = (n: Notice) => {
    // Move focus before the row unmounts, so the remote never points at nothing.
    const next = unread.find((x) => x.id !== n.id)
    void setFocus(next ? `notice:${next.id}` : 'hero:play')
    read.mutate(n.id)
  }
  return (
    <FocusGroup focusKey="home:notices" as="section" className="home-notices" label="Notices" data-testid="notices">
      {unread.map((n) => (
        <div key={n.id} className="panel notice" data-testid="notice" data-kind={n.kind}>
          <Focusable focusKey={`notice:${n.id}`} className="notice-open fx-flat" role="link" label={n.title} onEnter={() => open(n)}>
            <strong>{n.title}</strong>
            {n.body && <span className="muted"> · {n.body}</span>}
          </Focusable>
          <Button focusKey={`notice:${n.id}:dismiss`} ghost icon="x" label={`Dismiss: ${n.title}`} onEnter={() => dismiss(n)}>Dismiss</Button>
        </div>
      ))}
    </FocusGroup>
  )
}
