import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from '../api/client'
import { useSession } from '../store/session'
import { Button } from '../components/ui'
import { Focusable, FocusGroup } from '../nav/Focusable'
import type { components } from '../api/schema'

type Capability = components['schemas']['RewindCapability']

/** No recording is started here. Enter explicitly requests one advertised archive position. */
export function RewindControls({ id, archived, onSeek }: { id: string; archived: boolean; onSeek: (at: string | null) => void }) {
  const profile = useSession((s) => s.profile?.id)
  const [minutes, setMinutes] = useState(1)
  const capability = useQuery({
    queryKey: ['rewind', profile, id], staleTime: 0, refetchInterval: 60_000,
    queryFn: ({ signal }) => unwrap<Capability>(api.GET('/playback/rewind', { signal, params: { query: { id } } })),
  })
  const available = capability.data?.mode === 'provider_archive'
  const seek = () => {
    const edge = capability.data?.advertised_end
    if (edge) onSeek(new Date(Date.parse(edge) - minutes * 60_000).toISOString())
  }
  return <FocusGroup focusKey="rewind:controls" className="flex gap-3 flex-wrap items-center" data-testid="rewind-controls">
    {available ? <>
      <span className="label">Provider archive · availability unverified</span>
      <Button focusKey="rewind:earlier" onEnter={() => setMinutes((m) => Math.min(60, m + 1))}>1 min earlier</Button>
      <Focusable focusKey="rewind:position" label={`Try ${minutes} minutes ago. Left and Right adjust, Select plays.`}
        onArrow={(direction) => { if (direction === 'left' || direction === 'right') { setMinutes((m) => Math.max(1, Math.min(60, m + (direction === 'left' ? 1 : -1)))); return false } return true }}
        onEnter={seek} className="chip">Try {minutes} min ago</Focusable>
      <Button focusKey="rewind:later" onEnter={() => setMinutes((m) => Math.max(1, m - 1))}>1 min later</Button>
    </> : <span className="label">{capability.isPending ? 'Checking archive capability…' : capability.error ? 'Archive capability unavailable.' : 'Rewind unavailable · recording disabled'}</span>}
    {archived && <Button focusKey="rewind:live" primary onEnter={() => onSeek(null)}>Go Live</Button>}
  </FocusGroup>
}
