import { shellBridge } from '../shell'
import { parseShellMessage, type StreamHubTVBridge } from '../shellProtocol'

export interface CastPlayback {
  id: string | null
  title: string
  poster: string | null
  playing: boolean
  position_ms: number
  duration_ms: number | null
  live: boolean
}
export interface CastCommand {
  seq: number
  request_id: string
  action: 'load' | 'play' | 'pause' | 'seek' | 'stop'
  id?: string | null
  variant_id?: string | null
  source_id?: string | null
  audio_track?: number | null
  catchup_programme_id?: string | null
  buffer_at?: string | null
  rewind_at?: string | null
  position_ms?: number | null
}
interface ReceiverBridge extends StreamHubTVBridge {
  castState(): string
  castCommand(payload: string): boolean
}
function bridge(): ReceiverBridge | null {
  const b = shellBridge() as ReceiverBridge | null
  return b && typeof b.castState === 'function' && typeof b.castCommand === 'function' ? b : null
}
export const nativeCastAvailable = () => bridge() !== null
export function receiverPlayback(): CastPlayback {
  const message = parseShellMessage(bridge()?.castState())
  const s = message?.kind === 'mini_player' ? message.state : null
  return {
    id: s?.id ?? null,
    title: s?.title ?? '',
    poster: s?.image ?? null,
    playing: s?.playing ?? false,
    position_ms: s?.position_ms ?? 0,
    duration_ms: s?.duration_ms ?? null,
    live: s?.live ?? false,
  }
}
export async function applyReceiverCommand(command: CastCommand): Promise<void> {
  const b = bridge()
  if (!b) throw new Error('The TV player is unavailable.')
  if (!b.castCommand(JSON.stringify(command))) throw new Error('The TV could not accept this command.')
  // Acknowledgment comes from the native player after the request has taken effect.
  const expires = Date.now() + (command.action === 'load' ? 90_000 : 8_000)
  while (Date.now() < expires) {
    const result = JSON.parse(b.castState()) as { request_id?: string; result?: string }
    if (result.request_id === command.request_id) {
      if (result.result === 'ok') return
      if (result.result === 'error') throw new Error('Playback could not be confirmed on the TV.')
    }
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  throw new Error('The TV has not confirmed this command.')
}
