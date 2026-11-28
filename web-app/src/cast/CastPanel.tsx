import { useEffect, useRef, useState } from 'react'
import { Button, Modal } from '../components/ui'
import { FocusGroup } from '../nav/Focusable'
import { useGo } from '../nav/back'
import { castRequest, sendCastCommand, setCastSession, useCastController, type CastSession } from './controller'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'
import { inShell } from '../shell'
import { nativeCastAvailable } from '../player/castNative'
import { ReceiverPanel } from './ReceiverPanel'
import { useCastPanelUi } from './panelUi'
import './cast.css'

type Target = { id: string; name: string }
const clock = (ms: number) => {
  const sec = Math.floor(Math.max(0, ms) / 1000)
  return sec >= 3600 ? [Math.floor(sec / 3600), Math.floor(sec / 60) % 60, sec % 60].map((n, i) => i ? String(n).padStart(2, '0') : n).join(':') : Math.floor(sec / 60) + ':' + String(sec % 60).padStart(2, '0')
}
export function CastPanel() {
  const profile = useSession(s => s.profile?.id)
  if (!profile) return null
  return inShell() ? (nativeCastAvailable() ? <ReceiverPanel key={profile} profile={profile} /> : null) : <SenderPanel key={profile} />
}

/** The visible entry belongs with this profile's settings, not in the global navigation. */
export function CastSettingsButton() {
  const session = useCastController((state) => state.session)
  const show = useCastPanelUi((state) => state.show)
  const receiver = inShell() && nativeCastAvailable()
  const label = receiver ? 'TV controls' : session ? `TV controls for ${session.target_name}` : 'Choose where to watch'
  return <Button focusKey="prof:cast" ghost icon="tv" onEnter={show} testId="cast-settings">{label}</Button>
}

function SenderPanel() {
  const open = useCastPanelUi((state) => state.open)
  const panelRevision = useCastPanelUi((state) => state.revision)
  const close = useCastPanelUi((state) => state.close)
  const [targets, setTargets] = useState<Target[]>([])
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [error, setError] = useState('')
  const { session, problem, outstanding, restoring } = useCastController()
  const revision = useRef(0)
  const toast = useUi(s => s.toast)
  const go = useGo()
  useEffect(() => () => { revision.current++ }, [])
  useEffect(() => {
    let active = true
    let timer = 0
    const ac = new AbortController()
    async function poll() {
      const rev = revision.current
      const valid = () => active && revision.current === rev
      let wait = 2000
      try {
        const state = await castRequest<{ session: CastSession | null }>('session', 'GET', undefined, ac.signal)
        if (!valid()) return
        setCastSession(state.session)
        if (open && !state.session) {
          const data = await castRequest<{ targets: Target[] }>('targets', 'GET', undefined, ac.signal)
          if (valid()) { setTargets(data.targets); setLoaded(true) }
        }
        if (valid()) setError('')
        if (!open && !state.session) wait = 30_000
      } catch (e) {
        if (valid()) { setError((e as Error).message); setLoaded(true) }
        wait = 5000
      } finally { if (active) timer = window.setTimeout(() => void poll(), wait) }
    }
    void poll()
    return () => { active = false; ac.abort(); window.clearTimeout(timer) }
  }, [open, panelRevision, refresh])
  const change = async (target?: Target) => {
    if (busy) return
    const rev = ++revision.current
    setBusy(true)
    setError('')
    try {
      if (target) {
        const next = await castRequest<{ session: CastSession }>('connect', 'POST', { target_id: target.id })
        if (revision.current === rev) setCastSession(next.session)
      } else if (session) {
        await castRequest('session/' + encodeURIComponent(session.id), 'DELETE')
        if (revision.current === rev) { setCastSession(null); useCastController.setState({ problem: null }); toast('Disconnected from ' + session.target_name) }
      }
    } catch (e) { if (revision.current === rev) setError((e as Error).message) }
    finally { if (revision.current === rev) { setBusy(false); setRefresh(x => x + 1) } }
  }
  const pb = session?.playback
  const connected = session?.status === 'connected'
  const waiting = !!outstanding && !problem
  const status = outstanding ? outstanding.payload.action === 'load' ? 'Starting playback on TV…' : 'Waiting for the TV…' : ''
  const seek = (delta: number) => {
    if (pb?.duration_ms) void sendCastCommand({ action: 'seek', position_ms: Math.max(0, Math.min(pb.duration_ms, pb.position_ms + delta)) })
  }
  return <>
    <Modal open={open} title={session ? session.target_name : 'Choose where to watch'} onClose={close} firstFocusKey="cast:close" variant="sheet">
      {session?.status === 'pending' && <p role="status" className="muted">Waiting for someone to accept on {session.target_name}.</p>}
      {connected && pb && <>
        <div className="cast-now">{pb.poster && <img src={pb.poster} alt="" />}<div>
          <strong>{pb.title || 'Ready to play'}</strong>
          {pb.id ? <p>{pb.live ? 'Live TV' : clock(pb.position_ms) + (pb.duration_ms ? ' / ' + clock(pb.duration_ms) : '')} · {pb.playing ? 'Playing' : 'Paused'}</p> : <p>Choose something to play on this TV.</p>}
        </div></div>
        {pb.id && <FocusGroup focusKey="cast:transport" className="lib-chips" label="Playback controls">
          {!!pb.duration_ms && !pb.live && <Button focusKey="cast:back" ghost disabled={waiting} onEnter={() => seek(-10_000)}>Back 10 s</Button>}
          <Button focusKey="cast:play" primary disabled={waiting} onEnter={() => void sendCastCommand({ action: pb.playing ? 'pause' : 'play' })}>{pb.playing ? 'Pause' : 'Play'}</Button>
          {!!pb.duration_ms && !pb.live && <Button focusKey="cast:forward" ghost disabled={waiting} onEnter={() => seek(10_000)}>Forward 10 s</Button>}
          <Button focusKey="cast:stop" ghost disabled={waiting} onEnter={() => void sendCastCommand({ action: 'stop' })}>Stop playback</Button>
        </FocusGroup>}
      </>}
      {(problem || error) && <div role="alert" className="recent-undo"><span>{problem || error}</span><Button focusKey="cast:retry" ghost onEnter={() => { if (outstanding) void sendCastCommand(outstanding.payload, true); else setRefresh(x => x + 1) }}>Retry</Button></div>}
      {!problem && !error && status && <p role="status">{status}</p>}
      {!session && <>
        <p className="muted">Choose a TV using this profile. Its viewer approves the connection.</p>
        <FocusGroup focusKey="cast:targets" className="acct-list">
          {targets.map(target => <Button key={target.id} focusKey={'cast:target:' + target.id} ghost icon="tv" disabled={busy} onEnter={() => void change(target)}>{target.name}</Button>)}
        </FocusGroup>
        {!loaded || restoring ? <p role="status">Looking for TVs…</p> : !targets.length && !error && <p role="status">No TVs available. Open TV controls on your TV and turn on phone control.</p>}
        <Button focusKey="cast:pair" ghost onEnter={() => { close(); go('/pair') }}>Connect a new TV</Button>
      </>}
      {session && <Button focusKey="cast:disconnect" ghost disabled={busy} onEnter={() => void change()}>{session.status === 'pending' ? 'Cancel request' : 'Disconnect phone'}</Button>}
      <Button focusKey="cast:close" primary onEnter={close}>{connected ? 'Continue browsing' : 'Close'}</Button>
    </Modal>
  </>
}
