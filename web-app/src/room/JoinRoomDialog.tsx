import { useState } from 'react'
import { Button, Modal } from '../components/ui'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { useGo } from '../nav/back'

/** Room codes: six characters from an alphabet without 0, O, 1 and I; either case is accepted. */
const CODE = /^[A-HJ-NP-Z2-9]{6}$/
/** A pasted link works too: the code is the last part of its path. */
export function roomCodeFrom(text: string): string {
  const linked = /\/room\/([A-Za-z0-9]{6})(?:[/?#]|$)/.exec(text)
  return (linked?.[1] ?? text).toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 6)
}

export function JoinRoomDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const go = useGo()
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const close = () => { setCode(''); setError(null); onClose() }
  const join = () => {
    if (!CODE.test(code)) { setError('A room code has six letters and numbers. It never contains 0, O, 1 or I.'); return }
    close()
    go(`/room/${code}`)
  }
  return (
    <Modal open={open} onClose={close} title="Join a room" firstFocusKey="joinroom:code" variant="sheet" testId="join-room">
      <p className="muted">Enter the six-character code from whoever started the room, or paste their link.</p>
      <FocusGroup focusKey="joinroom:form" className="flex flex-col gap-3">
        <FocusableInput focusKey="joinroom:code" label="Room code" placeholder="ABC234" autoCapitalize="characters" value={code} className="room-code-field"
          onChange={(v) => { setCode(roomCodeFrom(v)); setError(null) }} onSubmit={join} />
        {error && <p role="alert" style={{ color: 'var(--t-danger)' }}>{error}</p>}
        <div className="flex gap-3 flex-wrap">
          <Button focusKey="joinroom:go" primary icon="people" disabled={code.length !== 6} onEnter={join} testId="join-room-go">Join room</Button>
          <Button focusKey="joinroom:cancel" ghost onEnter={close}>Cancel</Button>
        </div>
      </FocusGroup>
    </Modal>
  )
}
