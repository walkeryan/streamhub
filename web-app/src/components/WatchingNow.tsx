import { usePublicRooms } from '../api/hooks'
import { useAccess } from '../api/me'
import type { Room } from '../api/types'
import { can } from '../lib/can'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { useGo } from '../nav/back'
import { Icon } from './Icon'
import { Button } from './ui'

/** What a banner calls the room's title: an episode by its show and number, anything else by its name. */
export const roomTitle = (room: Room) => room.title.kind === 'episode' && room.title.subtitle ? room.title.subtitle : room.title.title

const SHOWN = 2

/**
 * Public watch rooms someone else is hosting, at the top of Home: one press joins (the owner's decision:
 * someone invited to the server should not need a code). The server lists only rooms whose title this
 * profile may watch; a room this profile hosts, already sits in, or that is full is not offered.
 */
export function WatchingNow() {
  const access = useAccess()
  const allowed = access.permissions === undefined || can(access, 'rooms.join')
  const { data } = usePublicRooms(allowed)
  const go = useGo()
  const rooms = (data ?? []).filter((r) => !r.is_host && !r.is_member && r.members.length < r.max_members).slice(0, SHOWN)
  if (!allowed || !rooms.length) return null
  return (
    <FocusGroup focusKey="home:rooms" as="section" className="home-notices" label="Watch together" data-testid="watching-now">
      {rooms.map((room) => {
        const join = () => go(`/room/${room.code}?start=1`)
        const what = roomTitle(room)
        const watching = room.members.length
        return (
          <div key={room.code} className="panel notice watching-now" data-testid="public-room">
            <Focusable focusKey={`rooms:${room.code}`} className="notice-open fx-flat" role="link" label={`${room.host_name} is watching ${what}. Join`} onEnter={join}>
              <Icon name="people" />
              <strong>{room.host_name} is watching {what}</strong>
              {watching > 0 && <span className="muted"> · {watching} in the room</span>}
            </Focusable>
            <Button focusKey={`rooms:${room.code}:join`} primary icon="play" testId="public-room-join" onEnter={join}>Join</Button>
          </div>
        )
      })}
    </FocusGroup>
  )
}
