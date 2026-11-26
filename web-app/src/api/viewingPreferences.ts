import { useEffect } from 'react'
import { useProfilePreference } from './profilePreferences'
import { useUi } from '../store/ui'

export const artSizes = ['small', 'standard', 'large'] as const
type ArtSize = typeof artSizes[number]
const artSize = (raw: unknown) => artSizes.find((size) => size === raw) ?? null
export const useArtSize = () => useProfilePreference<ArtSize>('posterSize.v1', 'standard', artSize)

export const readingSizes = ['standard', 'large', 'largest'] as const
type ReadingSize = typeof readingSizes[number]
const readingSize = (raw: unknown) => readingSizes.find(size => size === raw) ?? null
export const useReadingSize = () => useProfilePreference<ReadingSize>('readingSize.v1', 'standard', readingSize)

export const captionSizes = ['small', 'standard', 'large'] as const
export const captionColors = ['white', 'yellow'] as const
export const captionBackgrounds = ['solid', 'dim', 'clear'] as const
type Captions = { size: ArtSize; color: typeof captionColors[number]; background: typeof captionBackgrounds[number] }
export const defaultCaptions: Captions = { size: 'standard', color: 'white', background: 'dim' }
function captions(raw: unknown): Captions | null {
  if (!raw || typeof raw !== 'object') return null
  const val = raw as Partial<Captions>
  const size = artSize(val.size)
  const color = captionColors.find((x) => x === val.color)
  const background = captionBackgrounds.find((x) => x === val.background)
  return size && color && background ? { size, color, background } : null
}
export const useCaptions = () => useProfilePreference('captions.v1', defaultCaptions, captions)

export function useViewingAppearance() {
  const { value: size } = useArtSize()
  const { value: caption } = useCaptions()
  const { value: reading } = useReadingSize()
  useEffect(() => {
    const attrs = document.documentElement.dataset
    attrs.readingSize = reading
    attrs.posterSize = size
    attrs.captionSize = caption.size
    attrs.captionColor = caption.color
    attrs.captionBackground = caption.background
  }, [size, reading, caption.size, caption.color, caption.background])
}

type RowPreferences = { order: string[]; hidden: string[] }
function rows(raw: unknown): RowPreferences | null {
  if (!raw || typeof raw !== 'object') return null
  const { order, hidden } = raw as Partial<RowPreferences>
  if (!Array.isArray(order) || !Array.isArray(hidden) || ![...order, ...hidden].every((id) => typeof id === 'string')) return null
  return { order, hidden }
}
const defaultRows: RowPreferences = { order: [], hidden: [] }
export function useHomeRows() {
  const legacy = rows(useUi((s) => s.homePrefs)) ?? defaultRows
  return useProfilePreference('homeRows.v1', legacy, rows, !!(legacy.order.length || legacy.hidden.length))
}
