import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams, useSearchParams } from 'react-router'
import { api, ApiError, unwrap } from '../api/client'
import { joinRoom, useRoomInfo } from '../api/hooks'
import type { Room } from '../api/types'
import { Button, Crossfade, Meta, Skeleton } from '../components/ui'
import { Icon } from '../components/Icon'
import { SafeImg } from '../components/SafeImg'
import { FocusGroup } from '../nav/Focusable'
import { useGo, useRestoreFocus } from '../nav/back'
import { setFocus } from '../nav/spatial'
import { busyHeading, busyLines, parseSourceBusy } from '../player/sourceBusy'
import { prefersHls } from '../player/usePlayback'
import { JoinRoomDialog } from '../room/JoinRoomDialog'
import { positionAt } from '../room/roomSync.ts'
import { RoomSession, seatToken } from '../room/session'
import { ShareSheet } from '../room/ShareSheet'
import { useAdaptiveAccent } from '../lib/theme'
import { fmtDuration } from '../lib/util'
import { useSession } from '../store/session'
import { Player } from './Player'

const KIND_LABEL: Record<string, string> = { movie: 'Movie', show: 'Series', episode: 'Episode', channel: 'Live channel' }

interface Refusal { title: string; lines: string[]; /** joining with the room's audio is the viewer's call */ consent?: boolean; gone?: boolean }

/** Why the room could not be shown or joined, in the server's words where it has some. */
function refusalOf(e: unknown): Refusal {
  if (!(e instanceof ApiError)) return { title: 'Could not reach the room', lines: [e instanceof Error ? e.message : 'Check your connection and try again.'] }
  const busy = parseSourceBusy(e.body)
  if (busy) return { title: busyHeading(busy), lines: [...busyLines(busy), 'Try again once a stream is free.'] }
  if (e.code === 'room_full') return { title: 'This room is full', lines: [e.message, 'Ask someone to leave, or start a room of your own.'] }
  if (e.code === 'room_unsupported') return { title: 'This cannot be watched together', lines: [e.message] }
  if (e.code === 'audio_selection_required') return { title: 'Check the audio first', lines: ['The version the room is watching is not in one of your audio languages, or its audio could not be verified.'], consent: true }
  if (e.status === 404) return { title: 'Room not found', lines: ['The code may be mistyped, or the room has ended. Rooms close when the host ends them, or after 30 minutes with nobody in them.'], gone: true }
  if (e.status === 401 || e.status === 403) return { title: 'You cannot join this room', lines: [e.message || 'Your profile is not allowed to watch this title or to join rooms.'] }
  return { title: 'Could not join the room', lines: [e.message] }
}

export function RoomScreen() {
  const code = (useParams().code ?? '').toUpperCase()
  const [sp] = useSearchParams()
  const profileId = useSession((s) => s.profile?.id)
  const at = Number(sp.get('at'))
  return <RoomFlow key={`${profileId}:${code}`} code={code} start={sp.get('start') === '1'} startAtMs={Number.isFinite(at) && at > 0 ? at : null} />
}

function RoomFlow({ code, start, startAtMs }: { code: string; start: boolean; startAtMs: number | null }) {
  const go = useGo()
  const [session, setSession] = useState<RoomSession | null>(null)
  // Each seat gets a player of its own: a rejoin starts from the new seat's stream.
  const [seat, setSeat] = useState(0)
  const info = useRoomInfo(code, !session)
  const room = info.data
  const [joining, setJoining] = useState(false)
  const [refusal, setRefusal] = useState<Refusal | null>(null)
  const [sharing, setSharing] = useState(false)
  const [other, setOther] = useState(false)
  const busy = useRef(false)
  useAdaptiveAccent(room?.title.backdrop ?? room?.title.poster)
  useRestoreFocus(!session && (!!room || !!info.error), room ? 'room:join' : 'room:other')

  const join = useCallback(async (known: Room, allowUnverifiedAudio = false) => {
    if (busy.current) return
    busy.current = true
    setJoining(true); setRefusal(null)
    try {
      const { generation } = await unwrap<{ generation: string }>(api.GET('/playback/generation'))
      // A seat this tab still holds (a reload, a connection that came back) is kept, not doubled.
      const joined = await joinRoom(code, { client: 'web', prefer_hls: prefersHls(), recovery_generation: generation, allow_unverified_audio: allowUnverifiedAudio, member_token: seatToken(code) })
      setSession(new RoomSession(known, joined, { generation, allowUnverifiedAudio, startAtMs }))
      setSeat((n) => n + 1)
    } catch (e) {
      setRefusal(refusalOf(e))
      window.setTimeout(() => void setFocus('room:join'), 30)
    } finally { busy.current = false; setJoining(false) }
  }, [code, startAtMs])

  // Start watching (the host, from the share sheet) and Rejoin skip the join screen.
  const started = useRef(false)
  useEffect(() => {
    if (!start || !room || started.current) return
    started.current = true
    void join(room)
  }, [start, room, join])
  // The seat lasts as long as this screen shows the room.
  useEffect(() => {
    if (!session) return
    session.retain()
    return () => session.release()
  }, [session])

  if (session) return <Player key={seat} room={session} onRejoin={() => { setSession(null); void join(session.room) }} />

  const failed = info.error && !room ? refusalOf(info.error) : null
  const shown = failed ?? refusal
  const full = !!room && room.members.length >= room.max_members
  const at = room ? positionAt(room.state, room.server_ms + (Date.now() - info.dataUpdatedAt)) : null
  return (
    <div className="room-join" data-testid="room-join" role="main">
      <div className="room-join-art" aria-hidden="true">
        <Crossfade src={room?.title.backdrop ?? room?.title.poster} />
        <div style={{ position: 'absolute', inset: 0, zIndex: 1, background: 'linear-gradient(to top, var(--t-bg) 6%, transparent 70%), linear-gradient(to right, var(--t-bg) 4%, transparent 70%)' }} />
      </div>
      <div className="page-pad title-page" style={{ position: 'relative', zIndex: 2 }}>
        {!room && !failed ? <div className="flex gap-8"><Skeleton style={{ width: '12rem', height: '18rem' }} /><div className="flex-1 flex flex-col gap-3"><Skeleton style={{ height: '3rem', width: '60%' }} /><Skeleton style={{ height: '6rem' }} /></div></div> : (
          <div className="title-top">
            {room && <div className="tile tile-poster title-poster">
              <SafeImg className="art" src={room.title.poster} alt="" fallback={<div className="grid place-items-center h-full p-4 text-center heading">{room.title.title}</div>} />
            </div>}
            <div className="title-head flex flex-col gap-3 min-w-0">
              <p className="label flex items-center gap-2"><Icon name="people" size={18} />Watch together · room {code}</p>
              <h1 className="display" data-testid="room-title">{room ? room.title.title : failed?.title}</h1>
              {room && <Meta items={[KIND_LABEL[room.title.kind], room.title.year, room.title.subtitle, room.live ? 'Live' : room.state.playing ? `Playing · ${fmtDuration((at ?? 0) / 1000)}` : at ? `Paused at ${fmtDuration(at / 1000)}` : 'Not started yet']} />}
            </div>
            <div className="title-body flex flex-col gap-4 min-w-0">
              {room && <>
                <p data-testid="room-host">{room.is_host ? 'You started this room.' : `${room.host_name} started this room.`}{room.controls_locked && !room.is_host ? ' Only the host controls playback.' : ''}</p>
                <div className="flex flex-col gap-2">
                  <span className="label">In the room · {room.members.length} of {room.max_members}</span>
                  <div className="flex gap-2 flex-wrap" data-testid="room-join-members">
                    {room.members.length ? room.members.map((m) => <span key={m.id} className="room-member" data-buffering={m.buffering}>{m.name}{m.host ? ' (host)' : ''}</span>) : <span className="muted">Nobody yet. The first one in starts it.</span>}
                  </div>
                </div>
              </>}
              {shown && (
                <div className="panel flex flex-col gap-2" role="alert" data-testid="room-refusal">
                  {room && <h2 className="heading">{shown.title}</h2>}
                  {shown.lines.filter(Boolean).map((line, i) => <p key={i} className={room || i ? 'muted' : undefined}>{line}</p>)}
                </div>
              )}
              <FocusGroup focusKey="room:actions" className="flex gap-3 flex-wrap items-center" preferred="room:join">
                {room && !shown?.gone && <Button focusKey="room:join" primary icon="play" disabled={joining || (full && !refusal)} onEnter={() => void join(room, !!refusal?.consent)} testId="room-join-go">
                  {joining ? 'Joining…' : refusal?.consent ? "Join with the room's audio" : full ? 'Room is full' : refusal ? 'Try again' : 'Join room'}
                </Button>}
                {room && !shown && <Button focusKey="room:invite" icon="people" onEnter={() => setSharing(true)} testId="room-join-invite">Invite</Button>}
                <Button focusKey="room:other" icon="search" onEnter={() => setOther(true)} testId="room-other">{room ? 'Another room' : 'Enter a code'}</Button>
                <Button focusKey="room:home" ghost onEnter={() => go('/')}>{room ? 'Not now' : 'Home'}</Button>
              </FocusGroup>
            </div>
          </div>
        )}
      </div>
      <ShareSheet room={sharing && room ? room : null} onClose={() => setSharing(false)} />
      <JoinRoomDialog open={other} onClose={() => setOther(false)} />
    </div>
  )
}
