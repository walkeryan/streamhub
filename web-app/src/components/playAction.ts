import { useEffect, useRef } from 'react'
import { useGo } from '../nav/back'
import { playbackOptions } from '../api/hooks'
import { grantAudioConsent } from '../player/audioConsent'
import { resolve } from '../player/usePlayback'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'
import type { SourceBadge, Tile } from '../api/types'

/** Start playback for a tile. In-app sources open the player directly; hand-off sources open the service's web URL. */
export function usePlayAction() {
  const go = useGo()
  const pending = useRef<AbortController | null>(null)
  useEffect(() => () => pending.current?.abort(), [])
  const toast = useUi((s) => s.toast)
  return async (t: Pick<Tile, 'id' | 'kind'>, source?: SourceBadge | null, opts?: { catchup?: string; variantId?: string; allowUnverifiedAudio?: boolean }) => {
    if (source && !source.playable_in_app) {
      try {
        const d = await resolve({ id: t.id, sourceId: source.source_id })
        const url = d.handoff?.web_url
        if (url) { window.open(url, '_blank', 'noopener'); toast(`Opened ${source.name} in a new tab.`) }
        else toast(`${source.name} cannot be opened from the web. Use the app on your TV or phone.`, 'error')
      } catch (e) { toast(e instanceof Error ? e.message : 'Could not open that service.', 'error') }
      return
    }
    pending.current?.abort()
    const controller = new AbortController()
    pending.current = controller
    const profileId = useSession.getState().profile?.id
    let variantId = opts?.variantId
    if (!variantId && t.kind !== 'channel' && !opts?.catchup) {
      try {
        const choices = await playbackOptions(t.id, source?.source_id, controller.signal)
        if (controller.signal.aborted || useSession.getState().profile?.id !== profileId) return
        if (!choices.automatic_variant_id) { go(`/title/${encodeURIComponent(t.id)}?choose=1`); return }
        variantId = choices.automatic_variant_id
      } catch (e) { if (controller.signal.aborted) return; toast(e instanceof Error ? e.message : 'Could not check audio. Choose a source from title details.', 'error'); return }
    }
    const q = new URLSearchParams()
    if (variantId) q.set('variant', variantId)
    if (opts?.allowUnverifiedAudio && variantId) q.set('audio_choice', grantAudioConsent(t.id, variantId, { sourceId: source?.source_id, catchupProgrammeId: opts.catchup }))
    if (source) q.set('source', source.source_id)
    if (opts?.catchup) q.set('catchup', opts.catchup)
    const qs = q.toString()
    go(`/play/${encodeURIComponent(t.id)}${qs ? `?${qs}` : ''}`)
  }
}
