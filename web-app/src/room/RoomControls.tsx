import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { authenticatedFetch } from '../api/client'
import { useSearch } from '../api/hooks'
import { Button, Modal } from '../components/ui'
import { useDebounced } from '../lib/util'
import { FocusableInput } from '../nav/Focusable'
import { seatToken } from './session'

type Controls = {
  queue: { id: string; title: string }[]
  members: { id: string; name: string; host: boolean }[]
  member_id: string
  is_host: boolean
  offered_to: string | null
}
async function controls(code: string, action?: string, id?: string): Promise<Controls> {
  const token = seatToken(code)
  if (!token) throw new Error('Rejoin the room to use its queue.')
  const result = await authenticatedFetch(
    new Request(new URL('/api/v1/rooms/' + encodeURIComponent(code) + '/controls', location.origin), {
      method: action ? 'POST' : 'GET',
      headers: { 'X-Room-Seat': token, 'Content-Type': 'application/json' },
      body: action ? JSON.stringify({ action, id: id ?? '' }) : undefined,
    }),
  )
  const body = await result.json()
  if (!result.ok) throw new Error(body.message ?? body.error?.message ?? 'The room could not apply that change.')
  return body
}
export function RoomControls({ code, onClose }: { code: string; onClose: () => void }) {
  const [text, setText] = useState('')
  const input = useDebounced(text, 250)
  const found = useSearch(input, [], false)
  const client = useQueryClient()
  const key = ['room-controls', code, seatToken(code)]
  const data = useQuery({ queryKey: key, queryFn: () => controls(code), refetchInterval: 3500 })
  const mutation = useMutation({
    mutationFn: ({ action, id }: { action: string; id?: string }) => controls(code, action, id),
    onSuccess: (value) => client.setQueryData(key, value),
  })
  const act = (action: string, id?: string) => mutation.mutate({ action, id })
  const s = data.data
  return (
    <Modal open title="Room queue and host" onClose={onClose} firstFocusKey="room-tools:search">
      <div className="room-tools">
        <FocusableInput
          focusKey="room-tools:search"
          value={text}
          onChange={setText}
          placeholder="Find a title to suggest"
          label="Find a title to suggest"
        />
        {found.data?.play_here.slice(0, 6).map((t) => (
          <Button
            key={t.id}
            focusKey={'suggest:' + t.id}
            disabled={mutation.isPending}
            onEnter={() => act('suggest', t.id)}
          >
            Suggest {t.title}
          </Button>
        ))}
        {(mutation.error || data.error) && <p role="alert">{(mutation.error || data.error)?.message}</p>}
        <h3>Up next</h3>
        {s?.queue.length === 0 && <p>Anyone in this room can suggest a title.</p>}
        {s?.queue.map((t) => (
          <div key={t.id}>
            <strong>{t.title}</strong>{' '}
            {s.is_host && (
              <>
                <Button
                  focusKey={'next:' + t.id}
                  disabled={mutation.isPending}
                  onEnter={() => act('advance', t.id)}
                >
                  Play for everyone
                </Button>
                <Button
                  focusKey={'remove:' + t.id}
                  disabled={mutation.isPending}
                  onEnter={() => act('remove', t.id)}
                >
                  Remove
                </Button>
              </>
            )}
          </div>
        ))}
        {s?.is_host && s.members.some((m) => !m.host) && (
          <>
            <h3>Offer host controls</h3>
            {s.members
              .filter((m) => !m.host)
              .map((m) => (
                <Button
                  key={m.id}
                  focusKey={'host:' + m.id}
                  disabled={s.offered_to === m.id || mutation.isPending}
                  onEnter={() => act('offer', m.id)}
                >
                  {s.offered_to === m.id ? 'Waiting for ' + m.name : m.name}
                </Button>
              ))}
          </>
        )}
        {s && s.offered_to === s.member_id && (
          <>
            <h3>Take over as host?</h3>
            <Button focusKey="room-tools:accept" disabled={mutation.isPending} onEnter={() => act('accept')}>
              Accept
            </Button>
            <Button focusKey="room-tools:decline" disabled={mutation.isPending} onEnter={() => act('decline')}>
              Decline
            </Button>
          </>
        )}
        <Button focusKey="room-tools:done" onEnter={onClose}>
          Done
        </Button>
      </div>
    </Modal>
  )
}
