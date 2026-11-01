import { useSession } from '../store/session'

// Deliberate audio consent belongs to one profile and playback choice. It is
// never restored from a URL or browser storage after reload/profile switching.
export interface AudioIntentScope { sourceId?: string | null; catchupProgrammeId?: string | null; rewindAt?: string | null }
const scopeKey = (scope: AudioIntentScope) => JSON.stringify([scope.sourceId ?? null, scope.catchupProgrammeId ?? null, scope.rewindAt ?? null])
const choices = new Map<string, { profileId: string; id: string; variantId: string; scope: string; expires: number }>()
useSession.subscribe((next, previous) => {
  if (next.profile?.id !== previous.profile?.id) choices.clear()
})
export function grantAudioConsent(id: string, variantId: string, scope: AudioIntentScope = {}): string {
  const profileId = useSession.getState().profile?.id
  if (!profileId) return ''
  const now = Date.now()
  for (const [token, choice] of choices) if (choice.expires <= now) choices.delete(token)
  while (choices.size >= 32) choices.delete(choices.keys().next().value!)
  const token = crypto.randomUUID()
  choices.set(token, { profileId, id, variantId, scope: scopeKey(scope), expires: now + 5 * 60_000 })
  return token
}
export function hasAudioConsent(token: string | null, id: string, variantId?: string, scope: AudioIntentScope = {}): boolean {
  const choice = token ? choices.get(token) : undefined
  return !!choice && choice.expires > Date.now() && choice.profileId === useSession.getState().profile?.id && choice.id === id && choice.variantId === variantId && choice.scope === scopeKey(scope)
}
