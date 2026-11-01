import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router'
import { useChannels } from '../api/hooks'
import { Button, Chip, ProgressBar } from '../components/ui'
import { ChannelRow } from '../components/ChannelRow'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { useBackHandler, useOverlayFocus } from '../nav/back'
import { setFocus } from '../nav/spatial'
import { ChannelAudioPrompt, useChannelAudio } from '../player/channelAudio'
import { grantAudioConsent, hasAudioConsent } from '../player/audioConsent'
import { usePlayback } from '../player/usePlayback'
import { LocalBufferControls } from '../player/LocalBufferControls'
import { RewindControls } from '../player/RewindControls'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'
import { fmtDuration, fmtTime, progressOf, useNow } from '../lib/util'

type Overlay = null | 'menu' | 'list' | 'bar'
type Aspect = 'contain' | 'cover' | 'fill'
const ASPECTS: Array<{ id: Aspect; label: string }> = [{ id: 'contain', label: 'Fit' }, { id: 'cover', label: 'Fill' }, { id: 'fill', label: 'Stretch' }]
const SLEEP = [null, 15, 30, 45, 60, 90, 120]

export function Player() {
  const id = decodeURIComponent(useParams().id ?? '')
  const [sp] = useSearchParams()
  const sourceId = sp.get('source')
  const variantId = sp.get('variant') ?? undefined
  const profileId = useSession((s) => s.profile?.id)
  const audioChoice = sp.get('audio_choice')
  const catchup = sp.get('catchup')
  const rewindAt = sp.get('rewind')
  const allowUnverifiedAudio = useMemo(() => !!profileId && hasAudioConsent(audioChoice, id, variantId, { sourceId, catchupProgrammeId: catchup, rewindAt }), [profileId, audioChoice, id, variantId, sourceId, catchup, rewindAt])
  const locationKey = useLocation().key
  const selectionKey = `${profileId}|${id}|${sp.toString()}|${locationKey}`
  const [manualChoice, setManualChoice] = useState<string | null>(null)
  const navigate = useNavigate()
  const videoRef = useRef<HTMLVideoElement>(null)
  const isChannel = id.startsWith('ch:')
  // Router URL updates can precede the next React commit. Remote events in
  // that gap must use the selected channel, not the previous render's id.
  const activeId = useRef(id)
  useLayoutEffect(() => { activeId.current = id }, [id])

  const req = useMemo(() => ({ id, sourceId, variantId, allowUnverifiedAudio, catchupProgrammeId: catchup, rewindAt }), [id, sourceId, variantId, allowUnverifiedAudio, catchup, rewindAt])
  const audio = useChannelAudio(req, manualChoice === selectionKey)
  const pb = usePlayback(videoRef, audio.request, { buffer: true })
  const { desc, state } = pb
  const live = desc?.id === id ? !!desc.live : isChannel && !catchup && !rewindAt

  const toast = useUi((s) => s.toast)
  const stored = useUi((s) => s.channelList)
  const setChannelList = useUi((s) => s.setChannelList)
  const setLast = useUi((s) => s.setLastChannelId)
  const sleepMinutes = useUi((s) => s.sleepMinutes)
  const setSleep = useUi((s) => s.setSleepMinutes)
  const fallback = useChannels({ limit: 80 })
  useEffect(() => { if (isChannel && stored.channels.length === 0 && fallback.data) setChannelList(fallback.data.list_id ?? null, fallback.data.channels ?? []) }, [isChannel, stored.channels.length, fallback.data, setChannelList])
  const list = stored.channels
  const idx = list.findIndex((c) => c.id === id)
  const channel = idx >= 0 ? list[idx] : undefined

  const [overlay, setOverlayState] = useState<Overlay>(null)
  const overlayRef = useRef<Overlay>(null)
  overlayRef.current = overlay
  // The menu and the channel list can be opened from the control bar; closing them returns to the bar.
  const returnTo = useRef<Overlay>(null)
  const setOverlay = useCallback((o: Overlay) => {
    const cur = overlayRef.current
    if (o === 'menu' || o === 'list') returnTo.current = cur === 'bar' ? 'bar' : null
    if (o === 'bar' && cur !== 'bar') window.setTimeout(() => void setFocus('pb:play'), 20)
    if (o === null && cur === 'bar') void setFocus('pv:video')
    overlayRef.current = o
    setOverlayState(o)
  }, [])
  const closeOverlay = useCallback(() => {
    const back = overlayRef.current === 'bar' ? null : returnTo.current
    returnTo.current = null
    if (overlayRef.current === 'bar') void setFocus('pv:video')
    overlayRef.current = back
    setOverlayState(back)
  }, [])
  // Live starts with only the channel OSD so Up/Down zap straight away; VOD starts with the control bar visible.
  const [ui, setUi] = useState(() => !isChannel)
  const [osd, setOsd] = useState(true)
  const [paused, setPaused] = useState(true)
  const [time, setTime] = useState({ cur: 0, dur: 0, seekable: false })
  const [aspect, setAspect] = useState<Aspect>('contain')
  const [digits, setDigits] = useState('')
  const digitsRef = useRef('')
  const now = useNow(15_000)
  const blocking = audio.blocked || overlay !== null || state === 'error' || state === 'handoff'
  const blockingRef = useRef(false)
  blockingRef.current = blocking || activeId.current !== id
  const [backHint, setBackHint] = useState(false)

  // ---- activity-driven chrome ----
  const idle = useRef(0)
  const poke = useCallback(() => {
    setUi(true)
    window.clearTimeout(idle.current)
    idle.current = window.setTimeout(() => setUi(false), 4000)
  }, [])
  const osdTimer = useRef(0)
  const showOsd = useCallback(() => {
    setOsd(true)
    window.clearTimeout(osdTimer.current)
    osdTimer.current = window.setTimeout(() => setOsd(false), 4000)
  }, [])
  const hideChrome = useCallback(() => { window.clearTimeout(idle.current); setUi(false) }, [])
  // a zap shows the OSD only; the control bar stays hidden so repeated Up/Down keep zapping
  useEffect(() => { showOsd() }, [id, showOsd])
  useEffect(() => { if (!isChannel) poke(); return () => { window.clearTimeout(idle.current); window.clearTimeout(osdTimer.current) } }, [isChannel, poke])
  useEffect(() => { document.title = `${desc?.title ?? channel?.name ?? 'Playing'} · StreamHub`; return () => { document.title = 'StreamHub' } }, [desc?.title, channel?.name])

  // ---- video events ----
  useEffect(() => {
    const v = videoRef.current
    if (!v) return
    const t = () => setTime({ cur: v.currentTime, dur: Number.isFinite(v.duration) ? v.duration : 0, seekable: v.seekable.length > 0 })
    const p = () => { setPaused(v.paused); if (v.paused) setUi(true) }
    v.addEventListener('timeupdate', t); v.addEventListener('pause', p); v.addEventListener('play', p); v.addEventListener('loadedmetadata', t)
    return () => { v.removeEventListener('timeupdate', t); v.removeEventListener('pause', p); v.removeEventListener('play', p); v.removeEventListener('loadedmetadata', t) }
  }, [])

  const exit = useCallback(() => navigate(-1), [navigate])
  const togglePlay = () => { const v = videoRef.current; if (!v) return; if (v.paused) pb.resume(); else pb.pause() }
  const seekBy = useCallback((s: number) => {
    const v = videoRef.current
    if (!v || !v.seekable.length) return
    const wanted = v.currentTime + s
    let closest = v.currentTime, distance = Infinity
    for (let i = 0; i < v.seekable.length; i++) {
      const candidate = Math.max(v.seekable.start(i), Math.min(v.seekable.end(i), wanted))
      if (Math.abs(candidate - wanted) < distance) { closest = candidate; distance = Math.abs(candidate - wanted) }
    }
    v.currentTime = closest
  }, [])
  const pip = useCallback(async () => {
    const v = videoRef.current
    if (!v) return
    try {
      if (document.pictureInPictureElement) await document.exitPictureInPicture()
      else if (document.pictureInPictureEnabled) await v.requestPictureInPicture()
      else toast('Picture-in-picture is not supported in this browser.', 'error')
    } catch { toast('Picture-in-picture could not start.', 'error') }
  }, [toast])
  const fullscreen = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen()
    else void document.documentElement.requestFullscreen?.().catch(() => undefined)
  }, [])

  const zapTo = useCallback((cid: string) => {
    const previous = activeId.current
    if (cid === previous) return
    activeId.current = cid
    blockingRef.current = true
    setLast(previous)
    navigate(`/play/${encodeURIComponent(cid)}`, { replace: true })
  }, [navigate, setLast])
  const zap = useCallback((d: number) => {
    if (!list.length) return
    const currentIndex = list.findIndex((c) => c.id === activeId.current)
    const i = currentIndex < 0 ? 0 : (currentIndex + d + list.length) % list.length
    const c = list[i]
    if (c) zapTo(c.id)
  }, [list, zapTo])

  const showChrome = ui || paused || overlay === 'bar'
  const chromeRef = useRef(false)
  chromeRef.current = showChrome
  const pausedRef = useRef(false)
  pausedRef.current = paused

  // ---- keyboard (capture so it wins over spatial navigation while nothing modal is open) ----
  // Live: controls hidden -> Up/Down zap, OK opens the channel list, Left/Right reveal the controls.
  //       controls visible -> Down or OK moves focus into the control bar; Up still zaps.
  // In the bar: Left/Right move between buttons, OK activates, Up returns to the video, Back hides the controls.
  // VOD: Up/Down move into the bar, Left/Right seek, OK toggles play.
  // The handler is registered once and reads the latest callbacks through a ref, so a key pressed right after a
  // zap never sees stale state.
  // zapping with the controls hidden keeps them hidden (poke() runs on every key)
  const hideIfHidden = useCallback((visible: boolean) => { if (!visible) hideChrome() }, [hideChrome])
  const handlers = { id, live, isChannel, zap, zapTo, seekBy, togglePlay, pip, fullscreen, poke, showOsd, toast, hideIfHidden }
  const apiRef = useRef(handlers)
  apiRef.current = handlers
  useEffect(() => {
    let timer = 0
    const onKey = (e: KeyboardEvent) => {
      const a = apiRef.current
      // Back keys go to the back stack (useBackHandler below) and must not re-show the controls they are hiding
      if (isBackKey(e)) return
      const handled = () => { e.preventDefault(); e.stopImmediatePropagation() }
      if (chromeRef.current && document.activeElement?.closest('[data-testid="rewind-controls"], [data-testid="local-buffer-controls"]')) { a.poke(); return }
      // A zap updates the URL before React commits the next player. Keep the
      // keyboard last-channel shortcut out of the global Back stack in that gap.
      // Real remote Back still follows isBackKey above; open overlays keep Back.
      if (e.key === 'Backspace' && a.live && activeId.current !== a.id && overlayRef.current === null && !e.ctrlKey && !e.metaKey && !e.altKey) {
        handled()
        const last = useUi.getState().lastChannelId
        if (last) a.zapTo(last)
        return
      }
      if (overlayRef.current === 'bar' && e.key === 'ArrowUp') { handled(); setOverlay(null); a.poke(); return }
      const visible = chromeRef.current
      a.poke()
      if (blockingRef.current || e.ctrlKey || e.metaKey || e.altKey) return
      if (/^[0-9]$/.test(e.key) && a.live) {
        handled()
        digitsRef.current = (digitsRef.current + e.key).slice(-4)
        setDigits(digitsRef.current)
        window.clearTimeout(timer)
        timer = window.setTimeout(() => {
          const n = Number(digitsRef.current)
          digitsRef.current = ''; setDigits('')
          const c = useUi.getState().channelList.channels.find((x) => x.number === n)
          if (c) apiRef.current.zapTo(c.id); else apiRef.current.toast(`No channel ${n}.`)
        }, 2000)
        return
      }
      switch (e.key) {
        case 'ArrowUp': handled(); if (a.live) { a.hideIfHidden(visible); a.zap(1); a.showOsd() } else setOverlay('bar'); break
        case 'ArrowDown': handled()
          if (!a.live || visible) setOverlay('bar')
          else { a.hideIfHidden(visible); a.zap(-1); a.showOsd() }
          break
        case 'ArrowLeft': handled(); if (!a.live) a.seekBy(e.shiftKey ? -60 : -10); a.showOsd(); break
        case 'ArrowRight': handled(); if (!a.live) a.seekBy(e.shiftKey ? 60 : 10); a.showOsd(); break
        case 'Backspace': if (a.live) {
          handled()
          const last = useUi.getState().lastChannelId
          if (last) a.zapTo(last); else a.toast('No previous channel yet.')
        } break
        case 'Enter': handled(); if (a.live) setOverlay(visible ? 'bar' : 'list'); else a.togglePlay(); break
        case ' ': case 'k': handled(); a.togglePlay(); break
        case 'm': case 'M': case 'ContextMenu': case 'MediaTrackNext': handled(); setOverlay('menu'); break
        case 'p': case 'P': handled(); void a.pip(); break
        case 'f': case 'F': handled(); a.fullscreen(); break
        case 'i': case 'I': handled(); a.showOsd(); break
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => { window.removeEventListener('keydown', onKey, true); window.clearTimeout(timer) }
  }, [setOverlay])

  // Back: close an overlay (leaving the control bar also hides it), then hide the controls, then leave.
  // Live with a previous channel: a second Back within BACK_TWICE_MS returns to that channel instead of leaving.
  const backTimer = useRef(0)
  useEffect(() => () => window.clearTimeout(backTimer.current), [])
  useBackHandler(() => {
    if (audio.blocked || activeId.current !== id) { navigate('/live', { replace: true }); return true }
    const o = overlayRef.current
    if (o) { closeOverlay(); if (o === 'bar') hideChrome(); return true }
    if (chromeRef.current && !pausedRef.current) { hideChrome(); return true }
    const last = useUi.getState().lastChannelId
    if (!live || !last || last === activeId.current) return false
    if (backTimer.current) {
      window.clearTimeout(backTimer.current); backTimer.current = 0; setBackHint(false)
      zapTo(last); showOsd()
      return true
    }
    setBackHint(true)
    backTimer.current = window.setTimeout(() => { backTimer.current = 0; setBackHint(false); exit() }, BACK_TWICE_MS)
    return true
  })
  // menu and list remember and restore the focused control; the bar moves focus itself (setOverlay)
  useOverlayFocus(overlay === 'menu' || overlay === 'list', overlay === 'list' ? 'pl:current' : 'pm:first')

  useEffect(() => { if (pb.audioBlocked) setOverlay('menu') }, [pb.audioBlocked, setOverlay])

  // ---- sleep timer ----
  const [sleepAt, setSleepAt] = useState<number | null>(null)
  useEffect(() => { setSleepAt(sleepMinutes ? Date.now() + sleepMinutes * 60_000 : null) }, [sleepMinutes])
  useEffect(() => {
    if (!sleepAt) return
    const id2 = window.setTimeout(() => {
      videoRef.current?.pause(); setSleep(null); toast('Sleep timer ended. Playback stopped.'); navigate(-1)
    }, Math.max(0, sleepAt - Date.now()))
    return () => window.clearTimeout(id2)
  }, [sleepAt, setSleep, toast, navigate])

  const tr = pb.tracks()
  const title = desc?.title ?? channel?.name ?? 'Loading…'
  const pct = live ? (channel?.now ? progressOf(channel.now.start, channel.now.stop, now) : 0) : time.dur ? (time.cur / time.dur) * 100 : 0

  if (!audio.blocked && state === 'handoff' && desc?.handoff) {
    const h = desc.handoff
    return (
      <div className="player grid place-items-center p-6" data-testid="handoff" role="main">
        <FocusGroup focusKey="ho" className="flex flex-col gap-4 items-start" style={{ maxWidth: '34rem' }}>
          <h1 className="display">{desc.title}</h1>
          <p>This title plays in the {h.service_key} app. StreamHub opens it for you but cannot play it here.</p>
          <div className="flex gap-3">
            <Button focusKey="ho:open" primary icon="external" onEnter={() => window.open(h.web_url, '_blank', 'noopener')}>Open in app</Button>
            <Button focusKey="ho:back" onEnter={exit}>Back</Button>
          </div>
        </FocusGroup>
        <HandoffFocus />
      </div>
    )
  }

  return (
    <div className="player" data-testid="player" data-state={audio.blocked ? 'checking-audio' : state === 'playing' && desc?.id !== id ? 'resolving' : state} role="main" onPointerMove={poke} style={{ cursor: showChrome ? 'default' : 'none' }}>
      {/* focus target for "the video itself": Up from the control bar lands here, so no hidden button keeps focus */}
      <Focusable focusKey="pv:video" className="sr-only fx-none" scroll={false} label={title} role="presentation"><span /></Focusable>
      <video ref={videoRef} playsInline loop={live && desc?.mime === 'video/webm'} style={{ objectFit: aspect }} data-testid="video" aria-label={title} onDoubleClick={fullscreen} onClick={togglePlay} />

      {!audio.blocked && (state === 'resolving' || state === 'loading' || state === 'buffering' || state === 'reconnecting') && (
        <div className="grid place-items-center" style={{ position: 'absolute', inset: 0, zIndex: 2, pointerEvents: 'none' }} role="status">
          <div className="flex flex-col items-center gap-3"><span className="skel" style={{ width: 48, height: 48, borderRadius: 999 }} /><span>{state === 'reconnecting' ? 'Connection lost. Reconnecting…' : state === 'buffering' ? 'Buffering…' : 'Starting stream…'}</span></div>
        </div>
      )}

      {!audio.blocked && state === 'error' && (
        <div className="grid place-items-center p-6" style={{ position: 'absolute', inset: 0, zIndex: 8, background: 'rgba(0,0,0,.8)' }} role="alert" data-testid="player-error">
          <FocusGroup focusKey="perr" className="flex flex-col gap-4 items-start" style={{ maxWidth: '34rem' }}>
            <h1 className="title-xl">Playback stopped</h1>
            <p>{pb.error ?? 'The stream could not be played.'}</p>
            <div className="flex gap-3">
              <Button focusKey="perr:source" onEnter={() => { if (audio.enabled) { setManualChoice(selectionKey); void audio.query.refetch() } else navigate(`/title/${encodeURIComponent(id)}?choose=1`) }}>Choose audio / source</Button>
              <Button focusKey="perr:retry" primary icon="refresh" onEnter={pb.retryNow}>Try again</Button>
              <Button focusKey="perr:back" onEnter={exit}>Back</Button>
            </div>
          </FocusGroup>
          <HandoffFocus keyName="perr:retry" />
        </div>
      )}

      {backHint && <div className="numpad label" role="status" aria-live="assertive" data-testid="back-hint" style={{ fontSize: 'var(--text-body)', color: '#fff' }}>Press Back again for the last channel</div>}
      {digits && <div className="numpad" role="status" aria-live="assertive" data-testid="digits">{digits}</div>}
      {sleepAt && <div className="label" style={{ position: 'absolute', zIndex: 4, top: '1rem', right: 'var(--safe-x)', background: 'rgba(8,10,16,.7)', padding: '0.25rem 0.75rem', borderRadius: 999, color: '#fff' }}>Sleep in {Math.max(1, Math.round((sleepAt - now) / 60_000))} min</div>}

      {/* top bar + controls */}
      <div className="player-ui" data-show={showChrome} data-testid="player-ui">
        <div className="top">
          <FocusGroup focusKey="pt" className="flex gap-3 items-center">
            <Button focusKey="pt:back" iconOnly icon="back" label="Back" onEnter={exit} />
          </FocusGroup>
          <div className="min-w-0"><h1 className="title-xl truncate" data-testid="player-title">{title}</h1>{desc?.subtitle && <p className="label truncate" style={{ color: 'rgba(255,255,255,.75)' }}>{desc.subtitle}</p>}</div>
        </div>
        <div className="bottom">
          {!live && (
            <div className="flex items-center gap-3">
              <span className="label" style={{ color: '#fff' }}>{fmtDuration(time.cur)}</span>
              <Focusable focusKey="pb:scrub" role="slider" className="flex-1" disabled={!time.seekable}
                label={time.seekable ? `Seek ${fmtDuration(time.cur)} of ${fmtDuration(time.dur)}. Left and right move ten seconds.` : 'This stream has not supplied a seekable range.'}
                onArrow={(direction) => { if (direction === 'left' || direction === 'right') { seekBy(direction === 'left' ? -10 : 10); return false } return true }}
                onEnter={togglePlay}><ProgressBar pct={pct} /></Focusable>
              <span className="label" style={{ color: '#fff' }}>{fmtDuration(time.dur)}</span>
            </div>
          )}

          <FocusGroup focusKey="pb" className="flex gap-2 flex-wrap items-center" data-testid="player-bar">
            <Button focusKey="pb:play" primary iconOnly icon={paused ? 'play' : 'pause'} label={paused ? 'Play' : 'Pause'} onEnter={togglePlay} />
            {!live && <Button focusKey="pb:back10" disabled={!time.seekable} ghost onEnter={() => seekBy(-10)}>−10 s</Button>}
            {!live && <Button focusKey="pb:fwd10" disabled={!time.seekable} ghost onEnter={() => seekBy(10)}>+10 s</Button>}
            {isChannel && <Button focusKey="pb:ch-" ghost icon="down" label="Channel down" onEnter={() => zap(-1)} />}
            {isChannel && <Button focusKey="pb:ch+" ghost icon="up" label="Channel up" onEnter={() => zap(1)} />}
            {isChannel && <Button focusKey="pb:list" ghost icon="tv" onEnter={() => setOverlay('list')} testId="player-list-btn">Channels</Button>}
            <Button focusKey="pb:menu" ghost icon="cc" onEnter={() => setOverlay('menu')} testId="player-menu-btn">Audio and subtitles</Button>
            <Button focusKey="pb:pip" ghost icon="pip" label="Picture in picture" onEnter={() => void pip()} testId="player-pip">PiP</Button>
            <Button focusKey="pb:fs" ghost iconOnly icon="fullscreen" label="Full screen" onEnter={fullscreen} />
          {pb.bufferWindow && <LocalBufferControls window={pb.bufferWindow} issue={pb.bufferIssue} onSeek={pb.seekBuffer} />}
          {pb.bufferIssue && !pb.bufferWindow && <span role="status">{pb.bufferIssue}</span>}
          {isChannel && !pb.bufferWindow && <RewindControls key={id} id={id} archived={!live} onSeek={(at) => {
            const next = new URLSearchParams(sp); next.delete("catchup");
            if (at) next.set("rewind", at); else next.delete("rewind");
            navigate(`/play/${encodeURIComponent(id)}?${next}`, { replace: true });
          }} />}
          </FocusGroup>
        </div>
      </div>

      <ChannelAudioPrompt audio={audio} onCancel={() => navigate('/live', { replace: true })} onChoose={(option, explicit) => {
        const next = new URLSearchParams(sp)
        next.set('variant', option.variant_id)
        if (explicit) next.set('audio_choice', grantAudioConsent(id, option.variant_id, { sourceId, catchupProgrammeId: catchup, rewindAt }))
        else next.delete('audio_choice')
        navigate(`/play/${encodeURIComponent(id)}?${next}`, { replace: true })
      }} />

      {/* channel OSD */}
      {live && (
        <div className="osd" data-show={(osd || ui) && !(pb.bufferWindow && showChrome)} data-testid="osd">
          <div className="flex items-center gap-4">
            <span className="osd-num">{channel?.number ?? ''}</span>
            <div className="min-w-0 flex-1">
              <div className="heading truncate">{channel?.name ?? title}</div>
              <div className="truncate">{channel?.now ? `${channel.now.title}  ${fmtTime(channel.now.start)} to ${fmtTime(channel.now.stop)}` : 'No programme data'}</div>
              {channel?.next && <div className="label truncate" style={{ color: 'rgba(255,255,255,.7)' }}>Next · {channel.next.title} at {fmtTime(channel.next.start)}</div>}
              {!showChrome && <div className="label truncate" style={{ color: 'rgba(255,255,255,.7)' }}>▲▼ change channel · ◀ ▶ show controls · OK channel list</div>}
            </div>
          </div>
          {channel?.now && <ProgressBar pct={pct} />}
        </div>
      )}

      {/* audio / subtitle / aspect / sleep menu */}
      {overlay === 'menu' && (
        <div className="scrim" style={{ justifyItems: 'end', alignItems: 'stretch', padding: 0, background: 'rgba(0,0,0,.45)' }} onMouseDown={(e) => e.target === e.currentTarget && closeOverlay()}>
          <FocusGroup focusKey="pm" boundary className="dialog" role="dialog" label="Playback options" data-testid="player-menu" style={{ borderRadius: 0, width: 'min(26rem, 100%)', maxHeight: '100dvh', background: 'var(--t-surface)', color: 'var(--t-text)' }}>
            <div className="flex items-center justify-between"><h2 className="title-xl">Playback</h2><Button focusKey="pm:close" ghost iconOnly icon="x" label="Close" onEnter={closeOverlay} /></div>
            <MenuSection title="Audio">
              {pb.audioBlocked && <p role="alert" data-testid="runtime-audio-blocked">Playback paused: the selected audio language is unknown or does not match your preferences. Choose an audio track, or explicitly continue with the current audio.</p>}
              {pb.audioBlocked && <Button focusKey="pm:consent" onEnter={pb.consentToCurrentAudio}>Continue with current audio</Button>}
              {tr.audio.length === 0 ? <span className="muted label" style={{ flexBasis: '100%' }}>Audio track language is not exposed by this player.</span> : tr.audio.map((t, i) => (
                <Chip key={t.id} focusKey={i === 0 ? 'pm:first' : `pm:a${t.id}`} pressed={t.active} onEnter={() => pb.setAudio(t.id)}>{t.label}{!t.language || t.language === 'und' ? ' · language unknown' : ` · ${t.language}`}</Chip>
              ))}
            </MenuSection>
            <MenuSection title="Subtitles">
              <Chip focusKey={tr.audio.length ? 'pm:s-off' : 'pm:first'} pressed={!tr.subs.some((s) => s.active)} onEnter={() => pb.setSubtitle(-1)}>Off</Chip>
              {tr.subs.map((t) => <Chip key={t.id} focusKey={`pm:s${t.id}`} pressed={t.active} onEnter={() => pb.setSubtitle(t.id)}>{t.label}</Chip>)}
              {tr.subs.length === 0 && <span className="muted label" style={{ flexBasis: '100%' }}>No subtitle tracks in this stream.</span>}
            </MenuSection>
            <MenuSection title="Aspect ratio">
              {ASPECTS.map((a) => <Chip key={a.id} focusKey={`pm:asp:${a.id}`} pressed={aspect === a.id} onEnter={() => setAspect(a.id)}>{a.label}</Chip>)}
            </MenuSection>
            <MenuSection title="Sleep timer">
              {SLEEP.map((m) => <Chip key={m ?? 'off'} focusKey={`pm:sl:${m ?? 'off'}`} pressed={sleepMinutes === m} onEnter={() => { setSleep(m); if (m) toast(`Sleep timer set for ${m} minutes.`) }}>{m ? `${m} min` : 'Off'}</Chip>)}
            </MenuSection>
          </FocusGroup>
        </div>
      )}

      {/* OK on live: mini channel list */}
      {overlay === 'list' && (
        <div className="scrim" style={{ justifyItems: 'start', alignItems: 'stretch', padding: 0, background: 'rgba(0,0,0,.35)' }} onMouseDown={(e) => e.target === e.currentTarget && closeOverlay()}>
          <FocusGroup focusKey="pl" boundary className="dialog" role="dialog" label="Channels" data-testid="player-list" style={{ borderRadius: 0, width: 'min(26rem, 100%)', maxHeight: '100dvh', background: 'var(--t-surface)', color: 'var(--t-text)' }}>
            <h2 className="title-xl">Channels</h2>
            <div className="flex flex-col gap-2">
              {list.map((c) => (
                <ChannelRow key={c.id} focusKey={c.id === id ? 'pl:current' : `pl::${c.id}`} channel={c} compact onEnter={() => { closeOverlay(); zapTo(c.id) }} />
              ))}
              {list.length === 0 && <p className="muted">No channel list loaded.</p>}
            </div>
          </FocusGroup>
        </div>
      )}
    </div>
  )
}

const BACK_TWICE_MS = 750
const BACK_KEYS = new Set(['Escape', 'BrowserBack', 'GoBack', 'XF86Back'])
const BACK_CODES = new Set([27, 10009, 461, 166]) // Esc, Tizen, webOS, Android
function isBackKey(e: KeyboardEvent): boolean {
  return BACK_KEYS.has(e.key) || BACK_CODES.has(e.keyCode)
}

function MenuSection({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="flex flex-col gap-2"><h3 className="label">{title}</h3><div className="flex gap-2 flex-wrap">{children}</div></section>
}

function HandoffFocus({ keyName = 'ho:open' }: { keyName?: string }) {
  useEffect(() => { const t = window.setTimeout(() => void setFocus(keyName), 50); return () => window.clearTimeout(t) }, [keyName])
  return null
}
