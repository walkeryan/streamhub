import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, ok } from '../api/client'
import type { Tile } from '../api/types'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'
import { Button } from './ui'

export function useContinueVisibility() {
  const client = useQueryClient()
  const profile = useSession(s => s.profile?.id)
  const toast = useUi(s => s.toast)
  const [removed, setRemoved] = useState<{ tile: Tile; profile?: string } | null>(null)
  const change = async (tile: Tile, hidden: boolean) => {
    try {
      await ok(api.PUT('/history/continue-watching', { params: { query: { id: tile.id, hidden } } }))
      if (useSession.getState().profile?.id !== profile) return
      setRemoved(hidden ? { tile, profile } : null)
      await client.invalidateQueries({ queryKey: ['home'] })
    } catch { toast('Could not update Continue Watching. Try again.', 'error') }
  }
  const notice = removed && profile === removed.profile && <div className="continue-undo">
    <span role="status">Removed {removed.tile.title} from Continue Watching.</span>
    <Button focusKey="continue:undo" onEnter={() => void change(removed.tile, false)}>Undo</Button>
  </div>
  return { notice, remove: (tile: Tile) => void change(tile, true) }
}
