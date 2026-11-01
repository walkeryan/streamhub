import type { TrackInfo } from './engine'

export function audioLanguage(value?: string | null): string | null {
  const code = value?.trim().toLowerCase().replace('_', '-').split('-')[0]
  if (!code || ['und', 'unknown', 'zxx', 'mul'].includes(code)) return null
  return ({ eng: 'en', fra: 'fr', fre: 'fr', deu: 'de', ger: 'de', spa: 'es', ita: 'it', por: 'pt', jpn: 'ja' } as Record<string, string>)[code] ?? code
}

// Consent identifies actual selected tracks, including their metadata. A changed
// selection or manifest cannot inherit consent merely by reusing a numeric id.
export function audioSelection(tracks: readonly TrackInfo[]): string | null {
  if (!tracks.length) return 'unexposed'
  const selected = tracks.filter(t => t.active)
  return selected.length === 1 ? JSON.stringify([selected[0]!.id, selected[0]!.language ?? null, selected[0]!.label]) : null
}
export function audioAllowed(preferred: readonly string[], tracks: readonly TrackInfo[], consent: string | null): boolean {
  if (preferred.length === 0) return true
  const selected = audioSelection(tracks)
  if (selected !== null && consent === selected) return true
  const active = tracks.filter(t => t.active)
  const languages = preferred.map(audioLanguage).filter(Boolean)
  return active.length === 1 && !!audioLanguage(active[0]!.language) && languages.includes(audioLanguage(active[0]!.language))
}
export function preferredAudioTrack(preferred: readonly string[], tracks: readonly TrackInfo[]): number | null {
  for (const value of preferred) {
    const language = audioLanguage(value)
    const match = language && tracks.find(t => audioLanguage(t.language) === language)
    if (match) return match.id
  }
  return null
}
