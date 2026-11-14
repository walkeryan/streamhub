import { useEffect, useState } from 'react'
import { setRoomPublic } from '../api/hooks'
import QRCode from 'qrcode'
import type { Room } from '../api/types'
import { Button, Modal } from '../components/ui'
import { FocusGroup } from '../nav/Focusable'
import { useUi } from '../store/ui'

/** The link a guest opens: this site's join screen for the room. */
export const roomLink = (room: Pick<Room, 'join_path'>) => new URL(room.join_path, window.location.origin).toString()

async function copyText(text: string): Promise<boolean> {
  try { await navigator.clipboard.writeText(text); return true } catch { /* not a secure context, or refused: fall back */ }
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  let done: boolean
  try { done = document.execCommand('copy') } catch { done = false }
  area.remove()
  return done
}

/** How to get someone into a room: a link to send, a code to read out, a QR code to point a phone at. */
export function ShareSheet({ room, onClose, onStart, startLabel = 'Start watching' }: {
  room: Room | null; onClose: () => void
  /** Present on the way into the room; inside the room the sheet only closes. */
  onStart?: () => void; startLabel?: string
}) {
  const toast = useUi((s) => s.toast)
  const link = room ? roomLink(room) : ''
  // What the server last said; the switch answers at once and settles on the server's answer.
  const [open, setOpen] = useState<{ code: string; on: boolean } | null>(null)
  const isPublic = room ? (open?.code === room.code ? open.on : room.public ?? true) : true
  const togglePublic = async () => {
    if (!room) return
    const want = !isPublic
    setOpen({ code: room.code, on: want })
    try { setOpen({ code: room.code, on: (await setRoomPublic(room.code, want)).public ?? want }) }
    catch (e) { setOpen({ code: room.code, on: !want }); toast(e instanceof Error ? e.message : 'Could not change the room.', 'error') }
  }
  const [qr, setQr] = useState<{ link: string; src: string } | null>(null)
  useEffect(() => {
    if (!link) return
    let live = true
    QRCode.toDataURL(link, { margin: 1, width: 240 }).then((src) => { if (live) setQr({ link, src }) }).catch(() => undefined)
    return () => { live = false }
  }, [link])
  const copy = async () => {
    const copied = await copyText(link)
    toast(copied ? 'Link copied.' : 'Could not copy. Select the link and copy it by hand.', copied ? 'success' : 'error')
  }
  return (
    <Modal open={!!room} onClose={onClose} title="Watch together" firstFocusKey={onStart ? 'share:start' : 'share:copy'} variant="sheet" testId="room-share">
      {room && <>
        <p className="muted" data-testid="room-share-hint">{isPublic
          ? `Everyone signed in to this StreamHub sees this room on their Home screen and can join you${room.title.title ? ` for ${room.title.title}` : ''} with one press. The link and code work too.`
          : 'Only people you send the link or code to can join.'}</p>
        <div className="room-share">
          <div className="room-qr">{qr?.link === link && <img src={qr.src} alt="QR code for the room link" width={240} height={240} data-testid="room-qr" />}</div>
          <div className="flex flex-col gap-3 min-w-0">
            <div className="flex flex-col gap-1">
              <span className="label">Room code</span>
              <span className="room-code" data-testid="room-code" aria-label={`Room code ${room.code.split('').join(' ')}`}>{room.code}</span>
            </div>
            <div className="flex flex-col gap-1 min-w-0">
              <span className="label">Link</span>
              <span className="room-link" data-testid="room-link">{link}</span>
            </div>
          </div>
        </div>
        <FocusGroup focusKey="share:actions" className="flex gap-3 flex-wrap">
          {onStart && <Button focusKey="share:start" primary icon="play" onEnter={onStart} testId="room-start">{startLabel}</Button>}
          {room.is_host && <Button focusKey="share:public" icon={isPublic ? 'check' : 'people'} pressed={isPublic} onEnter={() => void togglePublic()} testId="room-public">{isPublic ? 'Public' : 'Private'}</Button>}
          <Button focusKey="share:copy" icon="copy" onEnter={() => void copy()} testId="room-copy">Copy link</Button>
          <Button focusKey="share:close" ghost onEnter={onClose}>{onStart ? 'Not now' : 'Done'}</Button>
        </FocusGroup>
      </>}
    </Modal>
  )
}
