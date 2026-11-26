import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ok, unwrap } from '../api/client'
import { useChannels } from '../api/hooks'
import type { Channel } from '../api/types'
import { Button, Chip, ErrorState, Modal } from '../ds'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'
import { useDebounced } from '../lib/util'

const PAGE = 20
type Action = 'top' | 'up' | 'down' | 'hide' | 'show'
export function ChannelLineup({ onClose }: { onClose: () => void }) {
  const profile = useSession(s => s.profile?.id)
  const toast = useUi(s => s.toast)
  const cache = useQueryClient()
  const [term, setTerm] = useState('')
  const [page, setPage] = useState(0)
  const [hidden, setHidden] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [reset, setReset] = useState(false)
  const query = useDebounced(term, 250)
  const visible = useChannels({ language: 'all', q: query, limit: PAGE, offset: page * PAGE }, !hidden)
  const concealed = useQuery({ enabled: hidden, queryKey: ['lineup-hidden', profile], queryFn: () => unwrap<Channel[]>(api.GET('/channels/lineup/hidden')) })
  const change = useMutation({
    mutationFn: (v: { channel: string; action: Action } | null) => ok(v
      ? api.POST('/channels/lineup', { body: { channel_id: v.channel, action: v.action } })
      : api.DELETE('/channels/lineup')),
    onSuccess: async (_, v) => {
      await Promise.all(['channels', 'lineup-hidden', 'channel-groups', 'home', 'sports'].map(key => cache.invalidateQueries({ queryKey: [key] })))
      setReset(false)
      if (!v || v.action === 'hide' || v.action === 'show') setSelected(null)
      if (!v) { setHidden(false); setPage(0); setTerm('') }
      toast('Channel lineup saved.', 'success')
    },
  })
  const matching = (concealed.data ?? []).filter(c => c.name.toLowerCase().includes(term.trim().toLowerCase()) || String(c.number) === term)
  const rows = hidden ? matching.slice(page * PAGE, (page + 1) * PAGE) : visible.data?.channels ?? []
  const total = hidden ? matching.length : visible.data?.total ?? 0
  const load = hidden ? concealed : visible
  const busy = change.isPending || load.isFetching
  const row = rows.find(c => c.id === selected)
  const selectTab = (value: boolean) => { setHidden(value); setPage(0); setSelected(null) }
  return <Modal open onClose={onClose} title="My channel lineup" firstFocusKey="lineup:search" variant="sheet">
    <FocusableInput focusKey="lineup:search" label="Find a channel" placeholder="Channel name or number" value={term} onChange={v => { setTerm(v); setPage(0); setSelected(null) }} />
    <FocusGroup focusKey="lineup:tabs" className="lib-chips">
      <Chip focusKey="lineup:visible" pressed={!hidden} onEnter={() => selectTab(false)}>Visible</Chip>
      <Chip focusKey="lineup:hidden" pressed={hidden} onEnter={() => selectTab(true)}>Hidden</Chip>
    </FocusGroup>
    {load.error ? <ErrorState error={load.error} onRetry={() => void load.refetch()} /> : <FocusGroup focusKey="lineup:list" className="flex flex-col gap-2">
      {rows.map(c => <Chip key={c.id} focusKey={`lineup:${c.id}`} pressed={selected === c.id} onEnter={() => setSelected(c.id)}>{c.number} · {c.name}</Chip>)}
      {!rows.length && <p role="status">{load.isPending ? 'Loading channels…' : 'No matching channels.'}</p>}
    </FocusGroup>}
    {row && <FocusGroup focusKey="lineup:actions" className="lib-chips" label={row.name}>
      {(hidden ? [['show', 'Show channel']] as const : [['up', 'Move up'], ['down', 'Move down'], ['top', 'Move to top'], ['hide', 'Hide channel']] as const).map(([action, label]) =>
        <Button key={action} focusKey={`lineup:${action}`} disabled={busy} onEnter={() => change.mutate({ channel: row.id, action })}>{label}</Button>)}
    </FocusGroup>}
    {change.error && <p role="alert">The lineup could not be saved. Try again.</p>}
    <FocusGroup focusKey="lineup:pages" className="lib-chips">
      {page > 0 && <Button focusKey="lineup:prev" onEnter={() => setPage(v => v - 1)}>Previous</Button>}
      {(page + 1) * PAGE < total && <Button focusKey="lineup:next" onEnter={() => setPage(v => v + 1)}>Next</Button>}
      <Button focusKey="lineup:reset" ghost disabled={busy} onEnter={() => setReset(v => !v)}>Restore default lineup</Button>
      <Button focusKey="lineup:done" onEnter={onClose}>Done</Button>
    </FocusGroup>
    {reset && <div className="lib-chips"><p>Restore the source order and show every channel hidden by this profile?</p>
      <Button focusKey="lineup:confirm" disabled={busy} onEnter={() => change.mutate(null)}>Restore defaults</Button>
      <Button focusKey="lineup:cancel" onEnter={() => setReset(false)}>Cancel</Button></div>}
  </Modal>
}
