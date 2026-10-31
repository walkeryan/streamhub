import { useGo } from '../nav/back'
import { resolve } from '../player/usePlayback'
import { useUi } from '../store/ui'
import type { SourceBadge, Tile } from '../api/types'

/** Start playback for a tile. In-app sources open the player directly; hand-off sources open the service's web URL. */
export function usePlayAction() {
  const go = useGo()
  const toast = useUi((s) => s.toast)
  return async (t: Pick<Tile, 'id' | 'kind'>, source?: SourceBadge | null, opts?: { catchup?: string }) => {
    if (source && !source.playable_in_app) {
      try {
        const d = await resolve({ id: t.id, sourceId: source.source_id })
        const url = d.handoff?.web_url
        if (url) { window.open(url, '_blank', 'noopener'); toast(`Opened ${source.name} in a new tab.`) }
        else toast(`${source.name} cannot be opened from the web. Use the app on your TV or phone.`, 'error')
      } catch (e) { toast(e instanceof Error ? e.message : 'Could not open that service.', 'error') }
      return
    }
    const q = new URLSearchParams()
    if (source) q.set('source', source.source_id)
    if (opts?.catchup) q.set('catchup', opts.catchup)
    const qs = q.toString()
    go(`/play/${encodeURIComponent(t.id)}${qs ? `?${qs}` : ''}`)
  }
}
