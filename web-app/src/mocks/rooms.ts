import { bypass, http, HttpResponse, ws } from 'msw'
import type { PlaybackDescriptor, Room } from '../api/types'
import { channels, profiles, seedFor, tileOf } from './fixtures'

/**
 * A small fake of the watch-room server: the REST calls and the room socket. It applies play, pause,
 * seek and lock, tracks who is in the room and who is buffering, and answers pings. A room on a title
 * only the provider has plays one shared stream, like the server's one upstream per room: every
 * member keeps one URL onto it, a seek starts it again (a new `stream_epoch` and `stream_start_ms` on
 * the `state`), and a seek to where it already starts keeps the epoch. A join that carries the member
 * token of a seat still in the room keeps that seat, and GET descriptor answers a member with their
 * descriptor as it is now. It does not run the buffering wait on its own: a test says `waiting` (or
 * any other server message) with a `say` change handed to `window.__mockRoomIn`.
 *
 * Rooms live in localStorage and every change is announced on a BroadcastChannel, so two tabs of one
 * browser share a room. Two separate browsers (two Playwright contexts) do the same when something
 * carries the changes between them: each is handed to `window.__mockRoomOut` when that exists, and
 * `window.__mockRoomIn` takes one in.
 */
const B = '/api/v1'
const KEY = 'mock.rooms'
const ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
const MAX_MEMBERS = 8
const MAX_ROOMS = 20
const SILENT_MS = 20_000
/** The fake server's clock runs this far ahead of the browser's, so a client that skips clock sync is visibly wrong. */
const SKEW_MS = 90_000
const serverNow = () => Date.now() + SKEW_MS

interface MockMember { id: string; profile_id: string; name: string; host: boolean; buffering: boolean; seen_ms: number }
interface MockRoom extends Omit<Room, 'members' | 'is_host' | 'server_ms'> {
  host_profile_id: string; members: MockMember[]
  /** One stream for the whole room (a source with a connection limit): it is restarted at every seek. */
  shared: boolean
  /** Which start of the shared stream is running, and where in the title it starts. */
  stream_epoch: number; stream_start_ms: number
}
/** One change to a room. Applying a change twice, or two of them in either order, ends in the same room. */
type Change =
  | { op: 'create'; room: MockRoom }
  | { op: 'join'; member: MockMember }
  | { op: 'leave'; member_id: string }
  | { op: 'state'; state: Room['state']; by: string | null; controls_locked: boolean; stream?: { epoch: number; start_ms: number } }
  | { op: 'buffering'; member_id: string; on: boolean }
  | { op: 'seen'; member_id: string; at: number }
  /** any server message, as is: how a test says `waiting` */
  | { op: 'say'; message: Record<string, unknown> }
  /** the server gives a member's seat up: silent, slow, replaced */
  | { op: 'drop'; member_id: string; reason: string }
  | { op: 'close'; reason: string }
interface Envelope { code: string; change: Change }
interface Client { send: (data: string) => void; close: (code?: number, reason?: string) => void }

const load = (): Record<string, MockRoom> => { try { return JSON.parse(localStorage.getItem(KEY) ?? '{}') as Record<string, MockRoom> } catch { return {} } }
const save = (rooms: Record<string, MockRoom>) => localStorage.setItem(KEY, JSON.stringify(rooms))
const error = (status: number, code: string, message: string) => HttpResponse.json({ code, message }, { status })
const profileOf = (req: Request) => profiles.find((p) => p.id === req.headers.get('authorization')?.replace('Bearer mock-access-', '')) ?? profiles[0]!
const newId = () => Array.from(crypto.getRandomValues(new Uint8Array(6)), (b) => b.toString(16).padStart(2, '0')).join('')
const newCode = () => Array.from(crypto.getRandomValues(new Uint8Array(6)), (b) => ALPHABET[b % ALPHABET.length]).join('')
const positionNow = (room: MockRoom) => room.state.position_ms === null ? null : room.state.position_ms + (room.state.playing ? Math.max(0, serverNow() - room.state.as_of_ms) : 0)
/** A member not heard from for 20 s is no longer in the room, as on the server. */
const present = (room: MockRoom) => room.members.filter((m) => serverNow() - m.seen_ms < SILENT_MS)
const membersMessage = (room: MockRoom) => ({ type: 'members', members: present(room).map(({ id, name, host, buffering }) => ({ id, name, host, buffering })) })
const stateMessage = (room: MockRoom, by: string | null) => ({
  type: 'state', ...room.state, by, controls_locked: room.controls_locked,
  ...(room.shared ? { stream_epoch: room.stream_epoch, stream_start_ms: room.stream_start_ms } : {}),
})
const roomOf = (code: string): MockRoom | undefined => load()[code.toUpperCase()]

const sockets = new Map<string, Map<string, Client>>()
/** closed{reason}, then the close frame once that message has been handed over. */
const end = (client: Client, reason: string, code: number) => { client.send(JSON.stringify({ type: 'closed', reason })); window.setTimeout(() => client.close(code), 0) }
/** Make the change to this browser's copy of the room and tell this page's sockets what the server would. */
function apply({ code, change }: Envelope): void {
  const rooms = load()
  const room = rooms[code]
  const messages: Array<Record<string, unknown>> = []
  if (change.op === 'create') { if (!room) rooms[code] = change.room }
  else if (!room) return
  else if (change.op === 'join') {
    if (!room.members.some((m) => m.id === change.member.id)) room.members.push(change.member)
    messages.push(membersMessage(room))
  } else if (change.op === 'leave') {
    room.members = room.members.filter((m) => m.id !== change.member_id)
    messages.push(membersMessage(room))
  } else if (change.op === 'state') {
    if (change.state.as_of_ms < room.state.as_of_ms) return
    room.state = change.state
    room.controls_locked = change.controls_locked
    if (change.stream) { room.stream_epoch = change.stream.epoch; room.stream_start_ms = change.stream.start_ms }
    room.waiting_for = null
    messages.push(stateMessage(room, change.by))
  } else if (change.op === 'buffering') {
    const member = room.members.find((m) => m.id === change.member_id)
    if (member) member.buffering = change.on
    messages.push(membersMessage(room))
  } else if (change.op === 'seen') {
    const member = room.members.find((m) => m.id === change.member_id)
    if (member) member.seen_ms = Math.max(member.seen_ms, change.at)
  } else if (change.op === 'drop') {
    const seat = sockets.get(code)?.get(change.member_id)
    if (seat) { sockets.get(code)?.delete(change.member_id); end(seat, change.reason, 4000) }
    room.members = room.members.filter((m) => m.id !== change.member_id)
    messages.push(membersMessage(room))
  } else if (change.op === 'say') {
    if (change.message.type === 'waiting') room.waiting_for = String(change.message.name ?? '')
    messages.push(change.message)
  } else delete rooms[code]
  save(rooms)
  for (const client of sockets.get(code)?.values() ?? []) {
    for (const message of messages) client.send(JSON.stringify(message))
    if (change.op === 'close') end(client, change.reason, 4000)
  }
  if (change.op === 'close') sockets.delete(code)
}
const channel = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel(KEY)
if (channel) channel.onmessage = (event: MessageEvent<Envelope>) => apply(event.data)
const relay = window as unknown as { __mockRoomOut?: (envelope: string) => void; __mockRoomIn?: (envelope: string) => void }
relay.__mockRoomIn = (json) => apply(JSON.parse(json) as Envelope)
/** Make a change here and announce it to the other tabs and browsers. */
function commit(code: string, change: Change): void {
  const envelope: Envelope = { code, change }
  apply(envelope)
  channel?.postMessage(envelope)
  relay.__mockRoomOut?.(JSON.stringify(envelope))
}

const publicRoom = (room: MockRoom, profileId: string): Room => {
  const { host_profile_id, members: _everyone, shared: _stream, stream_epoch: _epoch, stream_start_ms: _start, ...rest } = room
  return { ...rest, is_host: host_profile_id === profileId, server_ms: serverNow(), members: present(room).map(({ id, name, host, buffering }) => ({ id, name, host, buffering })) }
}

function titleOf(id: string): { title: Room['title']; playable: boolean; shared?: boolean } | null {
  const channel = channels.find((c) => c.id === id)
  if (channel) return { title: { id, kind: 'channel', title: channel.name, subtitle: `Channel ${channel.number}`, year: null, poster: channel.logo ?? null, backdrop: null }, playable: true }
  const seed = seedFor(id)
  if (!seed) return null
  const tile = tileOf(seed)
  const playable = tile.sources.filter((s) => s.playable_in_app)
  return {
    title: { id, kind: tile.kind, title: tile.title, subtitle: tile.subtitle ?? null, year: tile.year ?? null, poster: tile.poster ?? null, backdrop: tile.backdrop ?? null },
    // A title only the provider has plays through its limited connections: the room gets one stream for everyone.
    playable: playable.length > 0, shared: playable.length > 0 && playable.every((s) => s.source_type === 'xtream'),
  }
}

/**
 * The room's stream: the fixture clip, served with byte ranges. Passed through the mock service worker
 * the clip arrives as one whole answer, which a browser will play but not seek in; a room has to seek.
 */
const ROOM_CLIP = '/mock/room.webm'
let clip: Promise<ArrayBuffer> | null = null
const clipHandler = http.get(ROOM_CLIP, async ({ request }) => {
  clip ??= fetch(bypass('/mock/sample.webm')).then((r) => r.arrayBuffer())
  const bytes = await clip
  const range = /^bytes=(\d+)-(\d*)$/.exec(request.headers.get('range') ?? '')
  const start = range ? Math.min(Number(range[1]), bytes.byteLength - 1) : 0
  const end = range?.[2] ? Math.min(Number(range[2]), bytes.byteLength - 1) : bytes.byteLength - 1
  return new HttpResponse(bytes.slice(start, end + 1), {
    status: range ? 206 : 200,
    headers: { 'content-type': 'video/webm', 'accept-ranges': 'bytes', 'content-length': String(end - start + 1), ...(range ? { 'content-range': `bytes ${start}-${end}/${bytes.byteLength}` } : {}) },
  })
})

/** Positions the shared stream can start at, as on the server. */
const STREAM_STEP_MS = 5000
/** `mock-room.<code>.<member id>.<serial>`: the seat a member token names in this room, while it is still there and this profile's. */
function seatOf(room: MockRoom, token: string | null | undefined, profileId?: string): MockMember | null {
  const [prefix, code, memberId] = (token ?? '').split('.')
  if (prefix !== 'mock-room' || code !== room.code) return null
  return present(room).find((m) => m.id === memberId && (profileId === undefined || m.profile_id === profileId)) ?? null
}
let tokenSerial = 0
const tokenFor = (room: MockRoom, member: MockMember) => `mock-room.${room.code}.${member.id}.${++tokenSerial}`
/** A member token's life in seconds; a test shortens it to see the renewal. */
const tokenLife = () => Number(localStorage.getItem('mock.roomTokenLife')) || 7200
/** What this member plays the room with, as it is now: the room's position is where they start. */
function descriptorFor(room: MockRoom, member: MockMember): PlaybackDescriptor {
  return {
    // One URL per seat on the shared stream, the same across its restarts; a library title is each member's own file.
    id: room.id, mode: 'direct', url: room.shared ? `${ROOM_CLIP}?seat=${member.id}` : ROOM_CLIP, mime: 'video/webm', live: room.live, buffer_available: false, headers: {},
    title: room.title.title, subtitle: room.title.subtitle ?? null, poster: room.title.poster ?? null,
    resume_position_ms: Math.floor(positionNow(room) ?? 0), duration_ms: room.live ? null : room.shared ? 600_000 : 30_000,
    start_position_ms: room.shared ? room.stream_start_ms : null, stream_epoch: room.shared ? room.stream_epoch : null,
  }
}

const roomSocket = ws.link(/\/api\/v1\/rooms\/[A-Za-z0-9]+\/ws(\?|$)/)

export const roomHandlers = [
  clipHandler,
  http.post(`${B}/rooms`, async ({ request }) => {
    const me = profileOf(request)
    const body = (await request.json()) as { id: string; variant_id?: string | null; audio_track?: number | null }
    const found = titleOf(body.id)
    if (!found) return error(404, 'not_found', 'Title not found.')
    if (!found.playable) return error(409, 'room_unsupported', 'This title opens in another app, so it cannot be watched together.')
    if (Object.keys(load()).length >= MAX_ROOMS) return error(409, 'room_limit', 'Too many rooms are open right now. Try again in a little while.')
    const live = body.id.startsWith('ch:')
    const code = newCode()
    const room: MockRoom = {
      code, join_path: `/room/${code}`, id: body.id, live, variant_id: body.variant_id ?? null, audio_track: body.audio_track ?? null,
      host_profile_id: me.id, host_name: me.name, controls_locked: false, waiting_for: null, max_members: MAX_MEMBERS, members: [], shared: !!found.shared, stream_epoch: 1, stream_start_ms: 0,
      state: { playing: live, position_ms: live ? null : 0, as_of_ms: serverNow() }, title: found.title,
    }
    commit(code, { op: 'create', room })
    return HttpResponse.json(publicRoom(room, me.id), { status: 201 })
  }),
  http.get(`${B}/rooms/:code`, ({ params, request }) => {
    const room = roomOf(String(params.code))
    return room ? HttpResponse.json(publicRoom(room, profileOf(request).id)) : error(404, 'room_not_found', 'This room does not exist or has ended.')
  }),
  http.delete(`${B}/rooms/:code`, ({ params, request }) => {
    const me = profileOf(request)
    const room = roomOf(String(params.code))
    if (!room) return error(404, 'room_not_found', 'This room does not exist or has ended.')
    if (room.host_profile_id !== me.id && !me.is_admin) return error(403, 'forbidden', 'Only the host can close this room.')
    commit(room.code, { op: 'close', reason: 'deleted' })
    return new HttpResponse(null, { status: 204 })
  }),
  http.post(`${B}/rooms/:code/join`, async ({ params, request }) => {
    const me = profileOf(request)
    const room = roomOf(String(params.code))
    if (!room) return error(404, 'room_not_found', 'This room does not exist or has ended.')
    const body = (await request.json().catch(() => ({}))) as { member_token?: string | null }
    // The token of a seat still held keeps it; any other token is ignored and the join takes a new seat.
    let member = seatOf(room, body.member_token, me.id)
    if (member) commit(room.code, { op: 'seen', member_id: member.id, at: serverNow() })
    else {
      if (present(room).length >= MAX_MEMBERS) return error(409, 'room_full', `This room already has ${MAX_MEMBERS} members.`)
      member = { id: newId(), profile_id: me.id, name: me.name, host: room.host_profile_id === me.id, buffering: false, seen_ms: serverNow() }
      commit(room.code, { op: 'join', member })
    }
    return HttpResponse.json({ member_token: tokenFor(room, member), member_id: member.id, expires_in_seconds: tokenLife(), ws_path: `${B}/rooms/${room.code}/ws`, descriptor: descriptorFor(room, member) })
  }),
  // No bearer token: the member token in the query is the whole credential.
  http.get(`${B}/rooms/:code/descriptor`, ({ params, request }) => {
    const room = roomOf(String(params.code))
    const [prefix, code, memberId] = (new URL(request.url).searchParams.get('member_token') ?? '').split('.')
    if (prefix !== 'mock-room' || code !== String(params.code).toUpperCase() || !memberId) return error(401, 'room_unauthorized', 'This is not a member token of this room.')
    if (!room) return error(404, 'room_not_found', 'This room does not exist or has ended.')
    const member = present(room).find((m) => m.id === memberId)
    if (!member) return error(404, 'not_a_member', 'The room gave this seat up. Join again.')
    return HttpResponse.json(descriptorFor(room, member))
  }),

  roomSocket.addEventListener('connection', ({ client }) => {
    const code = /\/rooms\/([A-Za-z0-9]+)\/ws/.exec(client.url.pathname)?.[1]?.toUpperCase() ?? ''
    const [, tokenCode, memberId] = (client.url.searchParams.get('member_token') ?? '').split('.')
    const refuse = (reason: string, closeCode: number) => end(client, reason, closeCode)
    if (tokenCode !== code || !memberId) { refuse('unauthorized', 4401); return }
    const joined = roomOf(code)
    if (!joined) { refuse('not_found', 4404); return }
    if (!present(joined).some((m) => m.id === memberId)) { refuse('not_a_member', 4404); return }
    const seats = sockets.get(code) ?? new Map<string, Client>()
    sockets.set(code, seats)
    seats.set(memberId, client)
    client.send(JSON.stringify(membersMessage(joined)))
    client.send(JSON.stringify(stateMessage(joined, null)))
    if (joined.waiting_for) client.send(JSON.stringify({ type: 'waiting', name: joined.waiting_for }))

    const refused = (errorCode: string, message: string) => client.send(JSON.stringify({ type: 'error', code: errorCode, message }))
    client.addEventListener('message', (event) => {
      let message: { type?: string; t?: number; position_ms?: number; on?: boolean }
      try { message = JSON.parse(String(event.data)) as typeof message } catch { refused('bad_message', 'Not a JSON message.'); return }
      const room = roomOf(code)
      const me = room?.members.find((m) => m.id === memberId)
      if (!room || !me) return
      const clock = (playing: boolean, position_ms: number | null, stream?: { epoch: number; start_ms: number }) =>
        commit(code, { op: 'state', state: { playing, position_ms, as_of_ms: serverNow() }, by: me.id, controls_locked: room.controls_locked, stream })
      switch (message.type) {
        case 'ping':
          client.send(JSON.stringify({ type: 'pong', t: message.t, server_ms: serverNow() }))
          commit(code, { op: 'seen', member_id: me.id, at: serverNow() })
          break
        case 'play': case 'pause': case 'seek':
          if (room.controls_locked && !me.host) { refused('controls_locked', 'Only the host can control playback in this room.'); break }
          if (message.type === 'seek') {
            if (room.live) { refused('live_no_seek', 'A live channel has no position to seek to.'); break }
            if (!Number.isInteger(message.position_ms) || message.position_ms! < 0) { refused('bad_message', 'position_ms must be an integer of at least 0.'); break }
            // The shared stream starts again where the seek lands, unless it already starts there.
            const start = Math.floor(message.position_ms! / STREAM_STEP_MS) * STREAM_STEP_MS
            clock(room.state.playing, message.position_ms!, room.shared && start !== room.stream_start_ms ? { epoch: room.stream_epoch + 1, start_ms: start } : undefined)
          } else clock(message.type === 'play', positionNow(room))
          break
        case 'buffering':
          commit(code, { op: 'buffering', member_id: me.id, on: !!message.on })
          break
        case 'lock':
          if (!me.host) { refused('host_only', 'Only the host can lock the controls.'); break }
          // The clock itself does not change: the same state again, with the lock.
          commit(code, { op: 'state', state: room.state, by: me.id, controls_locked: !!message.on })
          break
        default:
          refused('bad_message', 'Unknown message type.')
      }
    })
    client.addEventListener('close', (event) => {
      if (sockets.get(code)?.get(memberId) === client) sockets.get(code)?.delete(memberId)
      // 1000 and 1001 leave at once; anything else keeps the seat until the silence rule drops it.
      if (event.code === 1000 || event.code === 1001) commit(code, { op: 'leave', member_id: memberId })
    })
  }),
]
