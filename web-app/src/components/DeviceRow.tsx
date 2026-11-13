import { useQueryClient } from '@tanstack/react-query'
import { absent } from '../api/client'
import { useCan } from '../api/me'
import { people, usePeopleChange, type AuthSession } from '../api/people'
import { DEVICE_KIND, ago, deviceName } from '../lib/people'
import { useUi } from '../store/ui'
import { Button, Chip } from './ui'

/**
 * One signed-in device: what it is, when it was last seen, Revoke, and for a holder of users.manage
 * the "shared household device" switch. `rename` adds Rename, for a person's own devices. A 404 from
 * either means the device has been signed out since the list was read.
 */
export const DEVICE_GONE = 'That device is no longer signed in.'
export function DeviceRow({ session: s, scope, rename, onRevoked }: { session: AuthSession; scope: string; rename?: () => void; onRevoked?: (s: AuthSession) => void }) {
  const toast = useUi((st) => st.toast)
  const qc = useQueryClient()
  const manages = useCan('users.manage')
  const revoke = usePeopleChange(() => people.revokeSession(s.sid))
  const share = usePeopleChange((on: boolean) => people.patchSession(s.sid, { shared_device: on }))
  const name = deviceName(s.label, s.kind, s.user_agent)
  return (
    <div className="person-row device-row" data-testid="device-row" data-current={s.current}>
      <span className="who">
        <span className="flex items-center gap-2 flex-wrap"><strong>{name}</strong>{s.current && <span className="badge badge-ok">This device</span>}{s.shared_device && <span className="badge">Shared</span>}</span>
        <span className="label">{[DEVICE_KIND[s.kind] ?? s.kind, `Last seen ${ago(s.last_seen_at)}`, `Signed in ${ago(s.created_at)}`].join(' · ')}</span>
      </span>
      {manages && <Chip focusKey={`${scope}:dev:${s.sid}:shared`} pressed={s.shared_device} label={`${name}: shared household device, ${s.shared_device ? 'on' : 'off'}`} testId="device-shared"
        onEnter={() => share.mutate(!s.shared_device, { onError: (e) => { toast(absent(e) ? DEVICE_GONE : e.message, 'error'); if (absent(e)) for (const queryKey of [['sessions'], ['users']]) void qc.invalidateQueries({ queryKey }) } })}>Shared household device</Chip>}
      {rename && <Button focusKey={`${scope}:dev:${s.sid}:rename`} label={`Rename ${name}`} onEnter={rename} testId="device-rename">Rename</Button>}
      <Button focusKey={`${scope}:dev:${s.sid}:revoke`} danger label={`Sign out ${name}`} disabled={revoke.isPending} testId="device-revoke"
        onEnter={() => revoke.mutate(undefined, { onSuccess: () => { toast(`${name} was signed out.`, 'success'); onRevoked?.(s) }, onError: (e) => toast(e.message, 'error') })}>Revoke</Button>
    </div>
  )
}
