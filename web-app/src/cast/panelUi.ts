import { create } from 'zustand'

type CastPanelUi = {
  open: boolean
  revision: number
  show: () => void
  close: () => void
}

/** The cast lifecycle stays mounted in the shell; Settings only asks its one global panel to open. */
export const useCastPanelUi = create<CastPanelUi>((set) => ({
  open: false,
  revision: 0,
  show: () => set((state) => ({ open: true, revision: state.revision + 1 })),
  close: () => set({ open: false }),
}))
