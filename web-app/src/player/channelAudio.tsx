import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { playbackOptions } from '../api/hooks'
import type { PlaybackOption } from '../api/types'
import { useSession } from '../store/session'
import { Button, Modal } from '../components/ui'
import { LANG_NAMES } from '../lib/util'
import type { PlaybackReq } from './usePlayback'

export function audioDescription(option: PlaybackOption): string {
  if (!option.audio_verified) return 'Audio unverified'
  const tracks = option.audio_languages.map((code) => LANG_NAMES[code] ?? code).join(', ')
  const defaultAudio = option.default_audio_language ? LANG_NAMES[option.default_audio_language] ?? option.default_audio_language : 'unverified'
  return `Verified tracks: ${tracks || 'unknown'} · Default audio: ${defaultAudio}`
}

export function useChannelAudio(req: PlaybackReq, forceChoice: boolean) {
  const profile = useSession((s) => s.profile)
  const enabled = req.id.startsWith('ch:') || req.id.startsWith('prog:')
  const query = useQuery({
    queryKey: ['playback-options', profile?.id, req.id, req.sourceId, profile?.languages.join(',')],
    enabled,
    queryFn: ({ signal }) => playbackOptions(req.id, req.sourceId ?? undefined, signal),
    staleTime: 0,
  })
  const automatic = query.data?.options.find((option) => option.variant_id === query.data?.automatic_variant_id)
  const requested = query.data?.options.find((option) => option.variant_id === req.variantId)
  const approved = req.variantId ? requested && (req.allowUnverifiedAudio || requested.variant_id === automatic?.variant_id) ? requested : undefined : automatic
  const blocked = enabled && (forceChoice || !approved)
  const variantId = enabled ? approved?.variant_id : req.variantId
  const request = useMemo(() => blocked ? null : { ...req, variantId }, [blocked, req, variantId])
  return { enabled, query, blocked, request }
}

export function ChannelAudioPrompt({ audio, onChoose, onCancel }: {
  audio: ReturnType<typeof useChannelAudio>; onChoose: (option: PlaybackOption, explicit: boolean) => void; onCancel: () => void;
}) {
  return <Modal open={audio.blocked} onClose={onCancel} title="Choose channel audio" firstFocusKey="channel-audio:cancel" wide>
    {audio.query.isPending ? <p role="status">Checking audio evidence before starting playback…</p> : audio.query.error ? <>
      <p role="alert">Audio could not be checked. No new stream has started.</p>
      <Button focusKey="channel-audio:retry" onEnter={() => void audio.query.refetch()}>Retry audio check</Button>
    </> : <>
      <p>Preferred audio: {audio.query.data?.preferred_languages.map((code) => LANG_NAMES[code] ?? code).join(', ') || 'All languages'}. A provider name or language hint does not prove spoken audio.</p>
      <p className="label">Choosing a source below explicitly accepts its listed audio for this playback. Cancel leaves this playback request without starting a new stream.</p>
      {audio.query.data?.options.map((option) => <Button key={option.variant_id} focusKey={`channel-audio:${option.variant_id}`} onEnter={() => onChoose(option, option.variant_id !== audio.query.data?.automatic_variant_id)}>
        <span>Play {option.source_name} · {audioDescription(option)}{option.language_hint ? ` · Provider hint: ${LANG_NAMES[option.language_hint] ?? option.language_hint}` : ''}<br /><span className="label">{option.provider_label}</span></span>
      </Button>)}
      {!audio.query.data?.options.length && <p>No authorized channel source is available.</p>}
    </>}
    <Button focusKey="channel-audio:cancel" onEnter={onCancel}>Cancel playback</Button>
  </Modal>
}
