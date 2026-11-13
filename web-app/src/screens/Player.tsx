import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent } from 'react'
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router'
import { createRoom, useChannels } from '../api/hooks'
import type { Room } from '../api/types'
import { Button, Chip, ProgressBar } from '../components/ui'
import { Icon } from '../components/Icon'
import { ChannelRow } from '../components/ChannelRow'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { hasPlaybackEntry, useBackHandler, useOverlayFocus } from '../nav/back'
import { getCurrentFocusKey, setFocus } from '../nav/spatial'
import { ChannelAudioPrompt, useChannelAudio } from '../player/channelAudio'
import { grantAudioConsent, hasAudioConsent } from '../player/audioConsent'
import { usePlayback } from '../player/usePlayback'
import { trackLabel } from '../player/audioTracks'
import { seekableRanges } from '../player/restartSeek'
import { Scrubber } from '../player/Scrubber'
import { ShortcutHelp } from '../player/ShortcutHelp'
import { endTarget, fractionTarget, seekHint } from '../player/seekSteps'
import { shortcutFor, type MenuSection, type ShortcutAction } from '../player/shortcuts'
import { SPEEDS, loadPrefs, nextSpeed, nextVolume, savePrefs, speedLabel, type PlayerPrefs } from '../player/playerPrefs'
import { useSeekControl } from '../player/useSeekControl'
import { busyHeading, busyLines } from '../player/sourceBusy'
import { LocalBufferControls } from '../player/LocalBufferControls'
import { RewindControls } from '../player/RewindControls'
import type { RoomSession } from '../room/session'
import { ShareSheet } from '../room/ShareSheet'
import { useRoomPlayer } from '../room/useRoomPlayer'
import { useSession } from '../store/session'
import { useCan } from '../api/me'
import { useUi } from '../store/ui'
import { fmtDuration, fmtTime, progressOf, useNow } from '../lib/util'
import { usePhone } from '../lib/layout'
import '../styles/player.css'

type Overlay = null | 'menu' | 'list' | 'bar'
type Aspect = 'contain' | 'cover' | 'fill'
const ASPECTS: Array<{ id: Aspect; label: string }> = [{ id: 'contain', label: 'Fit' }, { id: 'cover', label: 'Fill' }, { id: 'fill', label: 'Stretch' }]
const SLEEP = [null, 15, 30, 45, 60, 90, 120]
/** No input for this long while playing hides the controls. */
const CHROME_IDLE_MS = 3000
/** Two taps on one side of the picture within this are a skip, not two taps. */
const DOUBLE_TAP_MS = 300
type Range = [number, number]

/** `room`: play as a member of a watch room. The room supplies the stream and the transport controls go to it. */
export function Player({ room, onRejoin }: { room?: RoomSession; onRejoin?: () => void } = {}) {
  const routeId = decodeURIComponent(useParams().id ?? '')
  const id = room ? room.room.id : routeId
  const [sp] = useSearchParams()
  const sourceId = sp.get('source')
  const variantId = sp.get('variant') ?? undefined
  const profileId = useSession((s) => s.profile?.id)
  const audioChoice = sp.get('audio_choice')
  const audioParam = sp.get('audio')
  const audioTrack = audioParam !== null && /^\d{1,3}$/.test(audioParam) ? Number(audioParam) : null
  const catchup = sp.get('catchup')
  const rewindAt = sp.get('rewind')
  const allowUnverifiedAudio = useMemo(() => !!profileId && hasAudioConsent(audioChoice, id, variantId, { sourceId, catchupProgrammeId: catchup, rewindAt }), [profileId, audioChoice, id, variantId, sourceId, catchup, rewindAt])
  const location = useLocation()
  const locationKey = location.key
  const selectionKey = `${profileId}|${id}|${sp.toString()}|${locationKey}`
  const [manualChoice, setManualChoice] = useState<string | null>(null)
  const navigate = useNavigate()
  const cancelPending = () => {
    if (activeId.current === id && hasPlaybackEntry(location.state, location.pathname + location.search)) navigate(-1)
    else navigate('/live', {replace:true})
  }
  const videoRef = useRef<HTMLVideoElement>(null)
  const isChannel = id.startsWith('ch:')
  // A finished recording (rec:<id>) plays like a title, but has no title page to choose a source on.
  const isRecording = id.startsWith('rec:')
  // Router URL updates can precede the next React commit. Remote events in
  // that gap must use the selected channel, not the previous render's id.
  const activeId = useRef(id)
  useLayoutEffect(() => { activeId.current = id }, [id])

  const req = useMemo(() => ({ id, sourceId, variantId, allowUnverifiedAudio, catchupProgrammeId: catchup, rewindAt, audioTrack }), [id, sourceId, variantId, allowUnverifiedAudio, catchup, rewindAt, audioTrack])
  const channelAudio = useChannelAudio(req, manualChoice === selectionKey)
  // A room's audio was settled when the member joined; its stream comes from the room, without a local rewind buffer.
  const audio = room ? { ...channelAudio, enabled: false, blocked: false, request: req } : channelAudio
  const pb = usePlayback(videoRef, audio.request, { buffer: !room, descriptor: room?.descriptor })
  const { desc, state } = pb
  const live = desc?.id === id ? !!desc.live : isChannel && !catchup && !rewindAt
  // A restart-at-offset stream: the media element starts at `offset` seconds into the title and only
  // knows what the server has produced so far; the title's length comes from the descriptor.
  const restartable = desc?.id === id && desc.start_position_ms != null
  const offset = restartable ? (desc.start_position_ms ?? 0) / 1000 : 0
  const fullDuration = restartable && desc.duration_ms ? desc.duration_ms / 1000 : 0

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
  // `focus`: where in the bar a remote lands (the scrubber after OK paused a film, else Play).
  const setOverlay = useCallback((o: Overlay, focus = 'pb:play') => {
    const cur = overlayRef.current
    if (o === 'menu' || o === 'list') returnTo.current = cur === 'bar' ? 'bar' : null
    if (o === 'bar' && cur !== 'bar') window.setTimeout(() => void setFocus(focus), 20)
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
  const [ui, setUi] = useState(() => !isChannel || !!room)
  const [osd, setOsd] = useState(true)
  const [paused, setPaused] = useState(true)
  // `produced` is what a restart-at-offset stream can seek in without a new stream; `buffered` what is downloaded.
  const [time, setTime] = useState<{ cur: number; dur: number; seekable: boolean; buffered: Range[]; produced: Range[] }>({ cur: 0, dur: 0, seekable: false, buffered: [], produced: [] })
  useEffect(() => { setTime((t) => ({ ...t, cur: videoRef.current?.currentTime ?? 0 })) }, [desc])
  const [aspect, setAspect] = useState<Aspect>('contain')
  // The menu opens on what its button named: audio, subtitles, settings, or everything (the remote's Menu key).
  const [menuSection, setMenuSection] = useState<MenuSection>('all')
  const [help, setHelp] = useState(false)
  // Volume, mute, speed: this browser's, kept across titles.
  const [prefs, setPrefsState] = useState<PlayerPrefs>(loadPrefs)
  const prefsRef = useRef(prefs)
  const setPrefs = useCallback((change: Partial<PlayerPrefs>) => {
    const next = { ...prefsRef.current, ...change }
    prefsRef.current = next
    setPrefsState(next)
    savePrefs(next)
  }, [])
  // One line of feedback for a key that changes something unseen ("Volume 60%", "Subtitles off").
  const [flash, setFlash] = useState<string | null>(null)
  const flashTimer = useRef(0)
  const showFlash = useCallback((text: string) => {
    setFlash(text)
    window.clearTimeout(flashTimer.current)
    flashTimer.current = window.setTimeout(() => setFlash(null), 1400)
  }, [])
  useEffect(() => () => window.clearTimeout(flashTimer.current), [])
  const [digits, setDigits] = useState('')
  const digitsRef = useRef('')
  const now = useNow(15_000)
  // ---- watch room ----
  const duration = fullDuration || time.dur
  const canSeek = !live && (restartable ? fullDuration > 0 || time.seekable : time.seekable)
  const roomCtl = useRoomPlayer(room, videoRef, {
    state, audioBlocked: pb.audioBlocked, pause: pb.pause, resume: pb.resume, reload: pb.reload,
    offsetS: offset, durationS: duration, restartable,
  }, onRejoin)
  // ---- seeking: presses add up to one seek (seekSteps.ts); a room is asked instead of the element ----
  const roomSeekTo = roomCtl?.seekTo, roomCanControl = roomCtl?.canControl
  const roomSeek = useMemo(() => (roomSeekTo && roomCanControl ? { seekTo: roomSeekTo, canControl: roomCanControl } : null), [roomSeekTo, roomCanControl])
  const seek = useSeekControl(videoRef, { enabled: canSeek, offsetS: offset, durationS: duration, restartable, restart: pb.seekTo, room: roomSeek }, desc, state === 'error')
  // Watch together from the player: the room that was just made, shown with its link and code.
  const [together, setTogether] = useState<Room | null>(null)
  const [startingRoom, setStartingRoom] = useState(false)
  const mayStartRoom = useCan('rooms.create')
  // A phone's bar has two rows of 44 px targets: what does not fit moves into the settings menu.
  const phone = usePhone()
  const zappable = isChannel && !room
  // A dialog has the keys. The control bar does not: only its arrows and OK are navigation there.
  const blocking = audio.blocked || overlay === 'menu' || overlay === 'list' || help || state === 'error' || state === 'handoff' || !!roomCtl?.modal || !!together
  const blockingRef = useRef(false)
  blockingRef.current = blocking || activeId.current !== id
  const [backHint, setBackHint] = useState(false)

  // ---- activity-driven chrome ----
  const idle = useRef(0)
  // A pointer resting on the controls is using them.
  const overChrome = useRef(false)
  const poke = useCallback(() => {
    setUi(true)
    window.clearTimeout(idle.current)
    const hide = () => { if (overChrome.current) idle.current = window.setTimeout(hide, CHROME_IDLE_MS); else setUi(false) }
    idle.current = window.setTimeout(hide, CHROME_IDLE_MS)
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
    const buffered = (): Range[] => Array.from({ length: v.buffered.length }, (_, i) => [v.buffered.start(i), v.buffered.end(i)])
    const t = () => setTime({ cur: v.currentTime, dur: Number.isFinite(v.duration) ? v.duration : 0, seekable: v.seekable.length > 0, buffered: buffered(), produced: seekableRanges(v) })
    const p = () => { setPaused(v.paused); if (v.paused) setUi(true) }
    const moved = ['timeupdate', 'loadedmetadata', 'durationchange', 'progress', 'seeking', 'seeked'] as const
    for (const name of moved) v.addEventListener(name, t)
    v.addEventListener('pause', p); v.addEventListener('play', p)
    return () => { for (const name of moved) v.removeEventListener(name, t); v.removeEventListener('pause', p); v.removeEventListener('play', p) }
  }, [])

  // ---- volume, mute, speed ----
  const setSilenced = pb.setSilenced
  const silencedRef = useRef(setSilenced)
  silencedRef.current = setSilenced
  useEffect(() => { const v = videoRef.current; if (v) v.volume = prefs.volume }, [prefs.volume])
  useEffect(() => { silencedRef.current(prefs.muted) }, [prefs.muted])
  // A room plays at one speed for everyone, and live has no faster.
  const speed = live || room ? 1 : prefs.speed
  useEffect(() => {
    const v = videoRef.current
    if (!v) return
    // Loading a new stream puts the element back to normal speed.
    const apply = () => { if (v.playbackRate !== speed) v.playbackRate = speed }
    apply()
    v.addEventListener('loadedmetadata', apply)
    return () => v.removeEventListener('loadedmetadata', apply)
  }, [speed])
  const changeVolume = useCallback((volume: number) => {
    setPrefs({ volume, muted: volume === 0 })
    showFlash(volume === 0 ? 'Muted' : `Volume ${Math.round(volume * 100)}%`)
  }, [setPrefs, showFlash])
  const toggleMute = useCallback(() => {
    const now = prefsRef.current
    // Unmuting at zero would change nothing anyone can hear.
    const muted = !now.muted
    setPrefs(muted ? { muted } : { muted, volume: now.volume === 0 ? 0.5 : now.volume })
    showFlash(muted ? 'Muted' : `Volume ${Math.round((now.volume === 0 ? 0.5 : now.volume) * 100)}%`)
  }, [setPrefs, showFlash])
  const changeSpeed = useCallback((next: number) => {
    setPrefs({ speed: next })
    showFlash(`Speed ${speedLabel(next).toLowerCase()}`)
  }, [setPrefs, showFlash])

  const exit = useCallback(() => navigate(-1), [navigate])
  const leaveOrExit = roomCtl ? roomCtl.askLeave : exit
  const togglePlay = () => { if (roomCtl) { roomCtl.toggle(); return } const v = videoRef.current; if (!v) return; if (v.paused) pb.resume(); else pb.pause() }
  // OK on Play in the bar: once it plays again the remote is done there, so focus goes back to the picture and the bar may hide.
  const playFromBar = () => {
    const resuming = !!videoRef.current?.paused
    togglePlay()
    if (resuming && overlayRef.current === 'bar') { closeOverlay(); poke() }
  }
  const openMenu = useCallback((section: MenuSection) => { setMenuSection(section); setOverlay('menu') }, [setOverlay])
  const chooseSubtitle = (trackId: number, label?: string) => {
    pb.setSubtitle(trackId)
    if (trackId >= 0 && label) setPrefs({ subtitle: label })
  }
  // C: off when they are on, else the track used last (by its name, across titles), else the first.
  const toggleSubtitles = () => {
    const subs = pb.tracks().subs
    if (subs.some((t) => t.active)) { pb.setSubtitle(-1); showFlash('Subtitles off'); return }
    const wanted = subs.find((t) => t.label === prefsRef.current.subtitle) ?? subs[0]
    if (!wanted) { showFlash('No subtitles in this stream'); return }
    chooseSubtitle(wanted.id, wanted.label)
    showFlash(`Subtitles: ${wanted.label}`)
  }
  const startTogether = async () => {
    if (startingRoom) return
    setStartingRoom(true)
    try {
      setTogether(await createRoom({ id, variant_id: variantId ?? null, audio_track: desc?.id === id && desc.audio_track != null ? desc.audio_track : audioTrack }))
      setOverlay(null)
    } catch (e) { toast(e instanceof Error ? e.message : 'Could not start a room.', 'error') } finally { setStartingRoom(false) }
  }
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

  // The controls stay while they are in use: paused, a remote inside the bar, a menu open, a seek not landed yet.
  const seeking = seek.pending !== null || seek.heading !== null
  const showChrome = ui || paused || overlay === 'bar' || overlay === 'menu' || seeking
  const chromeRef = useRef(false)
  chromeRef.current = showChrome
  const pausedRef = useRef(false)
  pausedRef.current = paused
  // What held the controls has ended: they get the usual few seconds, not none.
  const held = overlay === 'menu' || seeking
  const wasHeld = useRef(false)
  useEffect(() => {
    if (wasHeld.current && !held) poke()
    wasHeld.current = held
  }, [held, poke])

  // ---- what a shortcut does (shortcuts.ts decides which key means what) ----
  const run = (action: ShortcutAction, repeat = false) => {
    const v = videoRef.current
    switch (action.type) {
      case 'toggle-play': togglePlay(); break
      case 'play': if (v?.paused) togglePlay(); break
      case 'pause': if (v && !v.paused) togglePlay(); break
      case 'stop': leaveOrExit(); break
      case 'seek-step': seek.step(action.dir, { held: repeat, big: action.big }); break
      case 'seek-fraction': seek.jumpTo(fractionTarget(action.digit, duration)); break
      case 'seek-start': seek.jumpTo(0); break
      case 'seek-end': seek.jumpTo(endTarget(duration)); break
      case 'speed': changeSpeed(nextSpeed(prefsRef.current.speed, action.dir)); break
      case 'mute': toggleMute(); break
      case 'volume': changeVolume(nextVolume(prefsRef.current.muted ? 0 : prefsRef.current.volume, action.dir)); break
      case 'subtitles': toggleSubtitles(); break
      case 'menu': openMenu(action.section); break
      case 'fullscreen': fullscreen(); break
      case 'pip': void pip(); break
      case 'info': showOsd(); break
      case 'help': setHelp(true); break
    }
  }

  // ---- keyboard (capture so it wins over spatial navigation while nothing modal is open) ----
  // Live: controls hidden -> Up/Down zap, OK opens the channel list, Left/Right reveal the controls.
  //       controls visible -> Down or OK moves focus into the control bar; Up still zaps.
  // A film: Left/Right add up to one seek, Up/Down are the volume, OK pauses and puts focus on the scrubber.
  // In the bar: the arrows move between controls (Left/Right on the scrubber seek), OK activates, Up goes to the
  // scrubber and then back to the picture, Back hides the controls. Letter keys work there as anywhere.
  // The handler is registered once and reads the latest callbacks through a ref, so a key pressed right after a
  // zap never sees stale state.
  // zapping with the controls hidden keeps them hidden (poke() runs on every key)
  const hideIfHidden = useCallback((visible: boolean) => { if (!visible) hideChrome() }, [hideChrome])
  // A room never zaps: its live channel takes the keys a title does.
  const handlers = { id, live: live && !room, titleLive: live, inRoom: !!room, canSeek, isChannel, zap, zapTo, togglePlay, run, seek, poke, showOsd, toast, hideIfHidden }
  const apiRef = useRef(handlers)
  apiRef.current = handlers
  useEffect(() => {
    let timer = 0
    const onKey = (e: KeyboardEvent) => {
      const a = apiRef.current
      // Back keys go to the back stack (useBackHandler below) and must not re-show the controls they are hiding
      if (isBackKey(e)) return
      // A text field keeps its keys.
      if (isTyping(e.target)) return
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
      const inBar = overlayRef.current === 'bar'
      if (inBar && e.key === 'ArrowUp' && !blockingRef.current) {
        a.poke()
        const row = barRow()
        // A second row of the bar (a phone, a channel's archive controls) is left by spatial navigation.
        if (row === 'lower') return
        handled()
        if (row === 'first' && a.canSeek) void setFocus('pb:scrub'); else setOverlay(null)
        return
      }
      const visible = chromeRef.current
      a.poke()
      if (blockingRef.current || e.ctrlKey || e.metaKey || e.altKey) return
      // OK goes to a pending seek target now instead of when the presses pause.
      if (e.key === 'Enter' && a.seek.hasPending()) { handled(); a.seek.commit(); return }
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
      const action = shortcutFor(e, { live: a.titleLive, room: a.inRoom, navigating: inBar, scrubbing: inBar && getCurrentFocusKey() === 'pb:scrub' })
      if (action) {
        handled()
        // A held key repeats a step or a volume change, never a toggle.
        if (!e.repeat || action.type === 'seek-step' || action.type === 'volume') a.run(action, e.repeat)
        return
      }
      // Inside the bar the arrows and OK belong to spatial navigation.
      if (inBar) return
      switch (e.key) {
        case 'ArrowUp': handled(); if (a.live) { a.hideIfHidden(visible); a.zap(1); a.showOsd() } else setOverlay('bar'); break
        case 'ArrowDown': handled()
          if (!a.live || visible) setOverlay('bar')
          else { a.hideIfHidden(visible); a.zap(-1); a.showOsd() }
          break
        case 'ArrowLeft': case 'ArrowRight': handled(); a.showOsd(); break
        case 'Backspace': if (a.live) {
          handled()
          const last = useUi.getState().lastChannelId
          if (last) a.zapTo(last); else a.toast('No previous channel yet.')
        } break
        case 'Enter': handled()
          if (a.live) setOverlay(visible ? 'bar' : 'list')
          else {
            // OK pauses, and a remote then stands on the scrubber: Left/Right to look for a place, Down for the buttons.
            const pausing = !a.titleLive && videoRef.current?.paused === false
            a.togglePlay()
            if (pausing) setOverlay('bar', a.canSeek ? 'pb:scrub' : 'pb:play')
          }
          break
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => { window.removeEventListener('keydown', onKey, true); window.clearTimeout(timer) }
  }, [setOverlay])

  // ---- media keys and the lock screen (Media Session) ----
  useEffect(() => {
    if (!('mediaSession' in navigator)) return
    const set = (action: MediaSessionAction, handler: MediaSessionActionHandler | null) => {
      try { navigator.mediaSession.setActionHandler(action, handler) } catch { /* an action this browser does not know */ }
    }
    const steps = live ? null : (dir: -1 | 1) => () => apiRef.current.run({ type: 'seek-step', dir, big: false })
    set('play', () => apiRef.current.run({ type: 'play' }))
    set('pause', () => apiRef.current.run({ type: 'pause' }))
    set('seekbackward', steps && steps(-1))
    set('seekforward', steps && steps(1))
    set('seekto', live ? null : (details) => { if (details.seekTime != null) apiRef.current.seek.seekNow(details.seekTime) })
    return () => { for (const action of ['play', 'pause', 'seekbackward', 'seekforward', 'seekto'] as const) set(action, null) }
  }, [live])
  const artwork = desc?.poster ?? channel?.logo ?? null
  const sessionTitle = desc?.title ?? channel?.name ?? ''
  const sessionSubtitle = desc?.subtitle ?? ''
  useEffect(() => {
    if (!('mediaSession' in navigator) || typeof MediaMetadata === 'undefined' || !sessionTitle) return
    try {
      navigator.mediaSession.metadata = new MediaMetadata({ title: sessionTitle, artist: sessionSubtitle, artwork: artwork ? [{ src: new URL(artwork, window.location.href).href }] : [] })
    } catch { /* artwork this browser will not take */ }
    return () => { navigator.mediaSession.metadata = null }
  }, [sessionTitle, sessionSubtitle, artwork])

  // Back: close an overlay (leaving the control bar also hides it), then hide the controls, then leave.
  // Live with a previous channel: a second Back within BACK_TWICE_MS returns to that channel instead of leaving.
  const backTimer = useRef(0)
  useEffect(() => () => window.clearTimeout(backTimer.current), [])
  useBackHandler(() => {
    if (audio.blocked || activeId.current !== id) { cancelPending(); return true }
    // A seek that has not been made yet is the first thing Back takes away.
    if (seek.cancel()) return true
    const o = overlayRef.current
    if (o) { closeOverlay(); if (o === 'bar') hideChrome(); return true }
    if (chromeRef.current && !pausedRef.current) { hideChrome(); return true }
    // Back out of a room asks first: leaving gives the seat up.
    if (roomCtl) { roomCtl.askLeave(); return true }
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
  const tr = pb.tracks()
  // The server encoded one track of the file: the others are a new stream away, not a player switch.
  const fileTracks = desc?.id === id && desc.audio_track != null ? desc.audio_tracks ?? [] : []
  // The audio menu of a stream that names no tracks has nothing to choose: focus starts on what it does have.
  const menuFirst = menuSection === 'audio' && !fileTracks.length && !tr.audio.length ? (pb.audioBlocked ? 'pm:consent' : 'pm:close') : 'pm:first'
  useOverlayFocus(overlay === 'menu' || overlay === 'list', overlay === 'list' ? 'pl:current' : menuFirst)

  useEffect(() => { if (pb.audioBlocked) openMenu('audio') }, [pb.audioBlocked, openMenu])

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

  const title = desc?.title ?? channel?.name ?? 'Loading…'
  // A committed seek shows where it is headed, not where the old stream still is.
  const position = seek.heading ?? offset + time.cur
  const pct = live && channel?.now ? progressOf(channel.now.start, channel.now.stop, now) : 0
  const loaded = (restartable ? time.produced : time.buffered).map(([from, to]): Range => [offset + from, offset + to])
  const hint = seek.pending ? seekHint(seek.pending.target - seek.pending.base) : null
  // Lock-screen and headset controls show the same place the bar does.
  const sessionAt = Math.floor(position / 5)
  useEffect(() => {
    if (!('mediaSession' in navigator) || live || !(duration > 0)) return
    try { navigator.mediaSession.setPositionState({ duration, position: Math.min(duration, Math.max(0, sessionAt * 5)), playbackRate: speed }) } catch { /* not supported, or a position it rejects */ }
  }, [live, duration, sessionAt, speed])

  // The channel banner is placed above the control bar, whose height depends on the screen and on what the channel offers.
  const rootRef = useRef<HTMLDivElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const bar = bottomRef.current
    if (!bar || !live || typeof ResizeObserver === 'undefined') return
    const measure = () => rootRef.current?.style.setProperty('--player-bar', `${bar.offsetHeight}px`)
    const watch = new ResizeObserver(measure)
    watch.observe(bar)
    measure()
    return () => watch.disconnect()
  }, [live, state])

  // ---- pointer and touch on the picture ----
  // A mouse click plays or pauses and a double click is full screen. A finger shows or hides the controls,
  // and two taps on the left or right third skip ten seconds (more taps add up, like the keys).
  const pointer = useRef({ kind: 'mouse', shown: false })
  const lastTap = useRef({ side: 0, at: 0 })
  const onPictureClick = (e: MouseEvent<HTMLVideoElement>) => {
    if (pointer.current.kind !== 'touch') { togglePlay(); return }
    const box = e.currentTarget.getBoundingClientRect()
    const x = box.width > 0 ? (e.clientX - box.left) / box.width : 0.5
    const side = x < 1 / 3 ? -1 : x > 2 / 3 ? 1 : 0
    const at = performance.now()
    const again = side !== 0 && lastTap.current.side === side && at - lastTap.current.at < DOUBLE_TAP_MS
    lastTap.current = { side, at }
    if (again && canSeek) { seek.step(side as -1 | 1); return }
    if (pointer.current.shown && !paused) hideChrome(); else poke()
  }

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
    <div className="player" ref={rootRef} data-testid="player" data-state={audio.blocked ? 'checking-audio' : state === 'playing' && desc?.id !== id ? 'resolving' : state} data-stream-start={restartable ? desc.start_position_ms ?? 0 : undefined} role="main"
      // A finger's taps are read on the picture (below); its drags on the controls keep them up through pointerdown.
      onPointerMove={(e) => { if (e.pointerType !== 'touch') poke() }} onPointerDown={poke} style={{ cursor: showChrome ? 'default' : 'none' }}>
      {/* focus target for "the video itself": Up from the control bar lands here, so no hidden button keeps focus */}
      <Focusable focusKey="pv:video" className="sr-only fx-none" scroll={false} label={title} role="presentation"><span /></Focusable>
      <video ref={videoRef} playsInline loop={live && desc?.mime === 'video/webm'} style={{ objectFit: aspect }} data-testid="video" aria-label={title}
        onPointerDown={(e) => { pointer.current = { kind: e.pointerType, shown: chromeRef.current } }}
        onDoubleClick={() => { if (pointer.current.kind !== 'touch') fullscreen() }} onClick={onPictureClick} />

      {!audio.blocked && (state === 'resolving' || state === 'loading' || state === 'buffering' || state === 'reconnecting') && (
        <div className="grid place-items-center" style={{ position: 'absolute', inset: 0, zIndex: 2, pointerEvents: 'none' }} role="status">
          <div className="flex flex-col items-center gap-3"><span className="skel" style={{ width: 48, height: 48, borderRadius: 999 }} /><span>{state === 'reconnecting' ? 'Connection lost. Reconnecting…' : state === 'buffering' ? 'Buffering…' : 'Starting stream…'}</span></div>
        </div>
      )}

      {!audio.blocked && state === 'error' && (
        <div className="grid place-items-center p-6" style={{ position: 'absolute', inset: 0, zIndex: 8, background: 'rgba(0,0,0,.8)' }} role="alert" data-testid="player-error">
          <FocusGroup focusKey="perr" className="flex flex-col gap-4 items-start" style={{ maxWidth: '34rem' }}>
            {pb.busy ? <>
              <h1 className="title-xl" data-testid="source-busy">{busyHeading(pb.busy)}</h1>
              {busyLines(pb.busy).map((line, i) => <p key={i}>{line}</p>)}
              <p className="muted">{pb.busy.active.length > 1 ? 'Try again once one of them has stopped.' : pb.busy.active.length ? 'Try again once that stream has stopped.' : 'Stop playback on another device, then try again.'}</p>
            </> : <>
              <h1 className="title-xl">Playback stopped</h1>
              <p>{pb.error ?? 'The stream could not be played.'}</p>
            </>}
            <div className="flex gap-3">
              {!pb.busy && !room && !isRecording && <Button focusKey="perr:source" onEnter={() => { if (audio.enabled) { setManualChoice(selectionKey); void audio.query.refetch() } else navigate(`/title/${encodeURIComponent(id)}?choose=1`) }}>Choose audio / source</Button>}
              <Button focusKey="perr:retry" primary icon="refresh" onEnter={pb.retryNow}>Try again</Button>
              <Button focusKey="perr:back" onEnter={leaveOrExit}>{room ? 'Leave room' : 'Back'}</Button>
            </div>
          </FocusGroup>
          <HandoffFocus keyName="perr:retry" />
        </div>
      )}

      {backHint && <div className="numpad label" role="status" aria-live="assertive" data-testid="back-hint" style={{ fontSize: 'var(--text-body)', color: '#fff' }}>Press Back again for the last channel</div>}
      {digits && <div className="numpad" role="status" aria-live="assertive" data-testid="digits">{digits}</div>}
      {seek.pending && hint && (
        <div className="seek-hint" data-side={seek.pending.target < seek.pending.base ? 'left' : 'right'} role="status" data-testid="seek-hint">
          <Icon name={seek.pending.target < seek.pending.base ? 'back10' : 'fwd10'} size={28} />{hint}
        </div>
      )}
      {flash && <div className="player-flash" role="status" aria-live="polite" data-testid="player-flash">{flash}</div>}
      {sleepAt && <div className="label" style={{ position: 'absolute', zIndex: 4, top: '1rem', right: 'var(--safe-x)', background: 'rgba(8,10,16,.7)', padding: '0.25rem 0.75rem', borderRadius: 999, color: '#fff' }}>Sleep in {Math.max(1, Math.round((sleepAt - now) / 60_000))} min</div>}

      {/* top bar + controls */}
      <div className="player-ui" data-show={showChrome} data-testid="player-ui">
        <div className="top">
          <FocusGroup focusKey="pt" className="flex gap-3 items-center">
            <Button focusKey="pt:back" iconOnly icon="back" label={room ? 'Leave room' : 'Back'} onEnter={leaveOrExit} />
          </FocusGroup>
          <div className="min-w-0"><h1 className="title-xl truncate" data-testid="player-title">{title}</h1>{desc?.subtitle && <p className="label truncate" style={{ color: 'rgba(255,255,255,.75)' }}>{desc.subtitle}</p>}</div>
          {roomCtl?.top}
        </div>
        <div className="bottom" ref={bottomRef} onPointerEnter={() => { overChrome.current = true }} onPointerLeave={() => { overChrome.current = false }}>
          {!live && (
            <Scrubber position={position} duration={duration} loaded={loaded} target={seek.pending?.target ?? null} hint={hint}
              busy={seek.heading !== null} disabled={!canSeek} onSeek={seek.seekNow} onStep={(dir) => seek.step(dir)}
              onEnter={() => { if (seek.hasPending()) seek.commit(); else playFromBar() }} />
          )}

          <FocusGroup focusKey="pb" className="pbar" data-testid="player-bar">
            <div className="pbar-row">
              <div className="pbar-group">
                <Button focusKey="pb:play" primary iconOnly icon={paused ? 'play' : 'pause'} label={paused ? 'Play' : 'Pause'} title={`${paused ? 'Play' : 'Pause'} (Space)`} onEnter={playFromBar} />
                {!live && <Button focusKey="pb:back10" disabled={!canSeek} ghost iconOnly icon="back10" label="Back 10 seconds" title="Back 10 seconds (←)" onEnter={() => seek.step(-1)} />}
                {!live && <Button focusKey="pb:fwd10" disabled={!canSeek} ghost iconOnly icon="fwd10" label="Forward 10 seconds" title="Forward 10 seconds (→)" onEnter={() => seek.step(1)} />}
                <div className="pvol">
                  <Button focusKey="pb:mute" ghost iconOnly icon={prefs.muted || prefs.volume === 0 ? 'mute' : 'audio'} pressed={prefs.muted} label={prefs.muted ? 'Unmute' : 'Mute'} title={`${prefs.muted ? 'Unmute' : 'Mute'} (M)`} onEnter={toggleMute} testId="player-mute" />
                  <input type="range" className="pvol-slider" min={0} max={100} step={5} tabIndex={-1} aria-label="Volume" data-testid="player-volume"
                    value={prefs.muted ? 0 : Math.round(prefs.volume * 100)} onChange={(e) => changeVolume(Number(e.target.value) / 100)}
                    // The arrows are seek and volume keys, not this slider's: give the keyboard back once the mouse lets go.
                    onPointerUp={(e) => e.currentTarget.blur()} />
                </div>
                {!live && (
                  <Button focusKey="pb:time" ghost className="ptime" testId="player-time" onEnter={() => setPrefs({ remaining: !prefs.remaining })}
                    label={`${fmtDuration(position)} of ${fmtDuration(duration)}. Select to show ${prefs.remaining ? 'the length' : 'the time remaining'}.`}>
                    {fmtDuration(position)} <span>/ {prefs.remaining ? `−${fmtDuration(Math.max(0, duration - position))}` : fmtDuration(duration)}</span>
                  </Button>
                )}
                {zappable && <Button focusKey="pb:ch-" ghost iconOnly icon="down" label="Channel down" title="Channel down (↓)" onEnter={() => zap(-1)} />}
                {zappable && <Button focusKey="pb:ch+" ghost iconOnly icon="up" label="Channel up" title="Channel up (↑)" onEnter={() => zap(1)} />}
                {zappable && <Button focusKey="pb:list" ghost icon="tv" onEnter={() => setOverlay('list')} testId="player-list-btn" {...(phone ? { iconOnly: true, label: 'Channels', title: 'Channels' } : { children: 'Channels' })} />}
              </div>
              <div className="pbar-group pbar-end">
                <Button focusKey="pb:subs" ghost iconOnly icon="cc" pressed={tr.subs.some((t) => t.active)} label="Subtitles" title="Subtitles (C turns them on or off)" onEnter={() => openMenu('subs')} testId="player-subs-btn" />
                <Button focusKey="pb:audio" ghost iconOnly icon="tracks" label="Audio" title="Audio (A)" onEnter={() => openMenu('audio')} testId="player-audio-btn" />
                <Button focusKey="pb:menu" ghost iconOnly icon="settings" label="Settings" title="Settings (S)" onEnter={() => openMenu('settings')} testId="player-menu-btn" />
                {!(phone && room) && <Button focusKey="pb:pip" ghost iconOnly icon="pip" label="Picture in picture" title="Picture in picture (P)" onEnter={() => void pip()} testId="player-pip" />}
                <Button focusKey="pb:fs" ghost iconOnly icon="fullscreen" label="Full screen" title="Full screen (F)" onEnter={fullscreen} />
                {roomCtl?.bar}
              </div>
            </div>
            {(pb.bufferWindow || pb.bufferIssue || zappable) && (
              <div className="pbar-extra">
                {pb.bufferWindow && <LocalBufferControls window={pb.bufferWindow} issue={pb.bufferIssue} onSeek={pb.seekBuffer} />}
                {pb.bufferIssue && !pb.bufferWindow && <span role="status">{pb.bufferIssue}</span>}
                {zappable && !pb.bufferWindow && <RewindControls key={id} id={id} archived={!live} onSeek={(at) => {
                  const next = new URLSearchParams(sp); next.delete("catchup");
                  if (at) next.set("rewind", at); else next.delete("rewind");
                  navigate(`/play/${encodeURIComponent(id)}?${next}`, { replace: true });
                }} />}
              </div>
            )}
          </FocusGroup>
        </div>
      </div>

      <ChannelAudioPrompt audio={audio} onCancel={cancelPending} onChoose={(option, explicit) => {
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
              {!showChrome && !room && <div className="label truncate" style={{ color: 'rgba(255,255,255,.7)' }}>▲▼ change channel · ◀ ▶ show controls · OK channel list</div>}
            </div>
          </div>
          {channel?.now && <ProgressBar pct={pct} />}
        </div>
      )}

      {/* audio / subtitles / settings menu: one panel, opened on the part its button named */}
      {overlay === 'menu' && (() => {
        const shows = (section: MenuSection) => menuSection === 'all' || menuSection === section
        // Focus starts on the first choice of whichever part is showing.
        let given = false
        const first = (key: string) => { if (given) return key; given = true; return 'pm:first' }
        return (
          <div className="scrim" style={{ justifyItems: 'end', alignItems: 'stretch', padding: 0, background: 'rgba(0,0,0,.45)' }} onMouseDown={(e) => e.target === e.currentTarget && closeOverlay()}>
            <FocusGroup focusKey="pm" boundary className="dialog" role="dialog" label={MENU_TITLE[menuSection]} data-testid="player-menu" data={{ section: menuSection }} style={{ borderRadius: 0, width: 'min(26rem, 100%)', maxHeight: '100dvh', background: 'var(--t-surface)', color: 'var(--t-text)' }}>
              <div className="flex items-center justify-between"><h2 className="title-xl">{MENU_TITLE[menuSection]}</h2><Button focusKey="pm:close" ghost iconOnly icon="x" label="Close" onEnter={closeOverlay} /></div>
              {shows('audio') && (
                <MenuSection title="Audio">
                  {pb.audioBlocked && <p role="alert" data-testid="runtime-audio-blocked">Playback paused: the selected audio language is unknown or does not match your preferences. Choose an audio track, or explicitly continue with the current audio.</p>}
                  {pb.audioBlocked && <Button focusKey="pm:consent" onEnter={pb.consentToCurrentAudio}>Continue with current audio</Button>}
                  {fileTracks.length > 0 ? fileTracks.map((t) => (
                    <Chip key={t.index} focusKey={first(`pm:f${t.index}`)} pressed={t.index === desc?.audio_track} onEnter={() => { if (t.index !== desc?.audio_track) { seek.setHeading(position); pb.selectAudioTrack(t.index) } closeOverlay() }}>{trackLabel(t)}</Chip>
                  )) : tr.audio.length === 0 ? <span className="muted label pmenu-note">Audio track language is not exposed by this player.</span> : tr.audio.map((t) => (
                    <Chip key={t.id} focusKey={first(`pm:a${t.id}`)} pressed={t.active} onEnter={() => pb.setAudio(t.id)}>{t.label}{!t.language || t.language === 'und' ? ' · language unknown' : ` · ${t.language}`}</Chip>
                  ))}
                </MenuSection>
              )}
              {shows('subs') && (
                <MenuSection title="Subtitles">
                  <Chip focusKey={first('pm:s-off')} pressed={!tr.subs.some((t) => t.active)} onEnter={() => chooseSubtitle(-1)}>Off</Chip>
                  {tr.subs.map((t) => <Chip key={t.id} focusKey={`pm:s${t.id}`} pressed={t.active} onEnter={() => chooseSubtitle(t.id, t.label)}>{t.label}</Chip>)}
                  {tr.subs.length === 0 && <span className="muted label pmenu-note">No subtitle tracks in this stream.</span>}
                </MenuSection>
              )}
              {shows('settings') && !live && !room && (
                <MenuSection title="Speed">
                  {SPEEDS.map((value) => <Chip key={value} focusKey={first(`pm:sp:${value}`)} pressed={prefs.speed === value} onEnter={() => changeSpeed(value)} testId="player-speed">{speedLabel(value)}</Chip>)}
                </MenuSection>
              )}
              {shows('settings') && (
                <MenuSection title="Aspect ratio">
                  {ASPECTS.map((a) => <Chip key={a.id} focusKey={first(`pm:asp:${a.id}`)} pressed={aspect === a.id} onEnter={() => setAspect(a.id)}>{a.label}</Chip>)}
                </MenuSection>
              )}
              {shows('settings') && !room && !catchup && !rewindAt && !isRecording && mayStartRoom && (
                <MenuSection title="Watch together">
                  <Button focusKey="pm:together" icon="people" disabled={startingRoom} onEnter={() => void startTogether()} testId="player-together">{startingRoom ? 'Starting a room…' : 'Watch together'}</Button>
                  <span className="muted label pmenu-note">Start a room for this {isChannel ? 'channel' : 'title'} and invite others with a link or code.</span>
                </MenuSection>
              )}
              {shows('settings') && (
                <MenuSection title="Sleep timer">
                  {SLEEP.map((m) => <Chip key={m ?? 'off'} focusKey={`pm:sl:${m ?? 'off'}`} pressed={sleepMinutes === m} onEnter={() => { setSleep(m); if (m) toast(`Sleep timer set for ${m} minutes.`) }}>{m ? `${m} min` : 'Off'}</Chip>)}
                </MenuSection>
              )}
              {shows('settings') && (
                <MenuSection title="More">
                  {phone && room && <Button focusKey="pm:pip" icon="pip" onEnter={() => { closeOverlay(); void pip() }}>Picture in picture</Button>}
                  <Button focusKey="pm:keys" icon="info" onEnter={() => { closeOverlay(); setHelp(true) }} testId="player-keys-btn">Keyboard shortcuts</Button>
                </MenuSection>
              )}
            </FocusGroup>
          </div>
        )
      })()}
      <ShortcutHelp open={help} live={live} onClose={() => setHelp(false)} />

      {roomCtl?.layers}
      <ShareSheet room={together} onClose={() => setTogether(null)}
        onStart={() => together && navigate(`/room/${together.code}?start=1${live ? '' : `&at=${Math.floor(position * 1000)}`}`, { replace: true })} />

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

const MENU_TITLE: Record<MenuSection, string> = { all: 'Playback', audio: 'Audio', subs: 'Subtitles', settings: 'Settings' }

/** Where remote focus is in the control bar: on the scrubber, in the first row of buttons, in a row below it, or elsewhere. */
function barRow(): 'scrub' | 'first' | 'lower' | 'out' {
  const focused = document.querySelector<HTMLElement>('.player-ui .bottom .fx[data-focused="true"]')
  if (!focused) return 'out'
  if (focused.closest('.scrub')) return 'scrub'
  const first = focused.closest('[data-testid="player-bar"]')?.querySelector<HTMLElement>('.fx')
  if (!first) return 'out'
  return focused.getBoundingClientRect().top < first.getBoundingClientRect().bottom - 4 ? 'first' : 'lower'
}

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target instanceof HTMLTextAreaElement || target.isContentEditable) return true
  // A range or a checkbox is not a place to type.
  return target instanceof HTMLInputElement && !['range', 'checkbox', 'radio', 'button'].includes(target.type)
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
