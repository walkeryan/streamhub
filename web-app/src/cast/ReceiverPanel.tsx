import { useEffect, useRef, useState } from 'react'
import { Button, Modal } from '../components/ui'
import { useSession } from '../store/session'
import { castRequest, type ReceiverSync } from './controller'
import { applyReceiverCommand, receiverPlayback } from '../player/castNative'

type Receipt = { session: string; seq: number; error?: string }
export function ReceiverPanel({ profile }: { profile: string }) {
  const preference = 'sh.cast.receiver:' + profile
  const receiptKey = preference + ':ack'
  const [enabled, setEnabled] = useState(() => localStorage.getItem(preference) === 'on')
  const [menu, setMenu] = useState(false)
  const [info, setInfo] = useState<ReceiverSync | null>(null)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [deciding, setDeciding] = useState(false)
  const active = useRef('')
  const running = useRef('')
  const ack = useRef<Receipt | null>(null)
  useEffect(() => {
    try { ack.current = JSON.parse(sessionStorage.getItem(receiptKey) ?? 'null') as Receipt | null } catch { ack.current = null }
  }, [receiptKey])
  useEffect(() => {
    if (!enabled) return
    let alive = true
    let timer = 0
    const abort = new AbortController()
    async function sync() {
      let delay = 2000
      try {
        const receipt = ack.current
        const next = await castRequest<ReceiverSync>('receiver/sync', 'POST', {
          enabled: true, session_id: active.current || undefined, playback: receiverPlayback(),
          ...(receipt && (!active.current || receipt.session === active.current) ? { session_id: receipt.session, ack: receipt.seq, error: receipt.error } : {}),
        }, abort.signal)
        if (!alive) return
        active.current = next.session?.id ?? ''
        setInfo(next)
        setError('')
        if (receipt && receipt.session !== active.current) { ack.current = null; sessionStorage.removeItem(receiptKey) }
        const command = next.command
        const sessionId = next.session?.id
        if (command && sessionId && next.session?.status === 'connected' && (!command.session_id || command.session_id === sessionId) &&
          !(ack.current?.session === sessionId && ack.current.seq >= command.seq) && !running.current) {
          const key = sessionId + ':' + command.seq
          running.current = key
          // A player launch can take longer than a heartbeat interval. Keep advertising while it opens.
          void (async () => {
            let failure: string | undefined
            try { await applyReceiverCommand(command) } catch { failure = 'The TV could not apply the playback command.' }
            if (active.current === sessionId && useSession.getState().profile?.id === profile) {
              ack.current = { session: sessionId, seq: command.seq, error: failure }
              sessionStorage.setItem(receiptKey, JSON.stringify(ack.current))
            }
            if (running.current === key) running.current = ''
          })()
        }
      } catch (e) { if (alive) setError((e as Error).message); delay = 4000 }
      finally { if (alive) timer = window.setTimeout(() => void sync(), delay) }
    }
    void sync()
    return () => { alive = false; abort.abort(); window.clearTimeout(timer) }
  }, [enabled, profile, receiptKey, refresh])
  const decide = async (accept: boolean) => {
    if (!info?.session || deciding) return
    setDeciding(true)
    try {
      await castRequest('receiver/decision', 'POST', { session_id: info.session.id, accept })
      if (!accept) { active.current = ''; setInfo(null) }
      setRefresh(n => n + 1)
    } catch (e) { setError((e as Error).message) }
    finally { setDeciding(false) }
  }
  const toggle = () => {
    localStorage.setItem(preference, enabled ? 'off' : 'on')
    if (enabled) {
      active.current = ''; ack.current = null; setInfo(null); sessionStorage.removeItem(receiptKey)
      void castRequest('receiver/sync', 'POST', { enabled: false }).catch(() => setError('Phone control is off here. The server will remove the TV when its connection expires.'))
    }
    setEnabled(!enabled); setError('')
  }
  const pending = info?.session?.status === 'pending'
  return <>
    <div className="cast-bar"><Button focusKey="cast:receiver" ghost icon="tv" onEnter={() => setMenu(true)}>{info?.session?.status === 'connected' ? 'Phone connected' : 'TV controls'}</Button></div>
    <Modal open={menu && !pending} title="TV controls" onClose={() => setMenu(false)} firstFocusKey="cast:receiver:toggle">
      <p>Allow a phone using this profile to request control of this TV. Each new connection needs your approval.</p>
      <Button focusKey="cast:receiver:toggle" onEnter={toggle}>{enabled ? 'Turn off phone control' : 'Allow phone control'}</Button>
      {enabled && <p role="status">{info?.controller_name ? 'Controlled by ' + info.controller_name : 'Available to phones using this profile.'}</p>}
      {info?.session?.status === 'connected' && <Button focusKey="cast:receiver:remove" ghost disabled={deciding} onEnter={() => void decide(false)}>Remove controller</Button>}
      {error && <p role="alert">{error}</p>}
      <Button focusKey="cast:receiver:close" ghost onEnter={() => setMenu(false)}>Close</Button>
    </Modal>
    <Modal open={!!pending} title="Allow this phone to control the TV?" onClose={() => void decide(false)} firstFocusKey="cast:decline">
      <p>{info?.controller_name || 'A phone'} wants to choose and control playback on this TV.</p>
      {error && <p role="alert">{error}</p>}
      <div className="lib-chips"><Button focusKey="cast:decline" ghost disabled={deciding} onEnter={() => void decide(false)}>Decline</Button><Button focusKey="cast:accept" primary disabled={deciding} onEnter={() => void decide(true)}>Allow connection</Button></div>
    </Modal>
  </>
}
