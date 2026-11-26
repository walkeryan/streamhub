import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from '../api/client'
import type { components } from '../api/schema'
import { Button } from '../ds'
import { fmtDuration, fmtTime } from '../lib/util'
import { useGo } from '../nav/back'
import { useSession } from '../store/session'
import { liveBookmark } from './liveBookmark'

export function LiveAlternatives({ channel, start, scope, onClose }: { channel: string; start: string; scope: string; onClose?: () => void }) {
  const profile = useSession(s => s.profile?.id)
  const go = useGo()
  const bookmark = profile ? liveBookmark(profile, channel, start) : undefined
  const options = useQuery({ queryKey: ['live-entry', profile, channel, start, bookmark], enabled: !!profile, retry: false, staleTime: 0,
    queryFn: ({ signal }) => unwrap<components['schemas']['LiveProgrammeEntry']>(api.GET('/channels/entry', { signal, params: { query: { channel_id: channel, start, bookmark } } })) })
  const data = options.data
  if (!data) return null
  const path = (place: 'start' | 'resume') => {
    const pos = place === 'start' ? data.buffer_start : data.buffer_resume
    if (pos) return `/play/${encodeURIComponent(channel)}?buffer_at=${encodeURIComponent(pos)}`
    if (data.recording_id && (place === 'start' || data.recording_resume_ms)) return `/play/${encodeURIComponent('rec:' + data.recording_id)}${place === 'start' ? '?recording_start=1' : ''}`
    return null
  }
  return <>{(['start', 'resume'] as const).map(place => {
    const target = path(place)
    if (!target) return null
    const at = data.buffer_resume ? fmtTime(data.buffer_resume) : fmtDuration((data.recording_resume_ms ?? 0) / 1000)
    return <Button key={place} focusKey={`${scope}:${place}`} onEnter={() => { onClose?.(); go(target) }}>
      {place === 'start' ? `Start over · ${fmtTime(start)}` : `Resume · ${at}`}
    </Button>
  })}</>
}
