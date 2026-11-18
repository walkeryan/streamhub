import { create } from 'zustand'
import type { MiniPlayerState } from '../shellProtocol'

/*
 * Where the player is when it is not full screen.
 *
 * In a browser the player lives above the router (PlayerHost): a /play address shows it full screen,
 * and minimising keeps the same player (and its <video>) playing as a bar at the bottom of the app while
 * the viewer browses. `mini` is what that bar plays: the id and query of the /play address it came from.
 *
 * Inside the Android TV app the TV's own player plays. While it is in picture-in-picture the TV tells
 * the page what it plays (`shell`), and the page shows the same bar for it (ShellMiniPlayer).
 */
export interface MiniSession { id: string; search: string }

interface DockState {
  mini: MiniSession | null
  /** Bumped whenever a new player starts, so the host gives it a fresh instance. */
  instance: number
  minimise: (session: MiniSession) => void
  /** Follow a change of title or stream made while minimised (the next episode, another channel). */
  update: (session: MiniSession) => void
  close: () => void
  fresh: () => void
  shell: MiniPlayerState | null
  /** When the TV last reported (Date.now()): the bar runs the clock on from there between reports. */
  shellAt: number
  setShell: (state: MiniPlayerState | null) => void
}

export const useDock = create<DockState>((set) => ({
  mini: null,
  instance: 0,
  minimise: (mini) => set({ mini }),
  update: (mini) => set((s) => (s.mini ? { mini } : {})),
  close: () => set({ mini: null }),
  fresh: () => set((s) => ({ mini: null, instance: s.instance + 1 })),
  shell: null,
  shellAt: 0,
  setShell: (shell) => set({ shell, shellAt: Date.now() }),
}))

/** History state an expand navigates with: the /play address it opens is the minimised player, not a new one. */
export const EXPAND_STATE = { expandMini: true } as const
export const isExpand = (state: unknown): boolean => !!state && typeof state === 'object' && (state as { expandMini?: unknown }).expandMini === true
