import { create } from 'zustand'

/**
 * "Who's watching?" on a shared household device. `picked` is false when the page loads, so the
 * picker opens the app; it stays true across a switch. `open` is the picker asked for again from
 * the profile entry. Memory only: every start of the app asks.
 */
export const useWho = create<{ picked: boolean; open: boolean; pick: () => void; show: () => void; hide: () => void }>((set) => ({
  picked: false,
  open: false,
  pick: () => set({ picked: true, open: false }),
  show: () => set({ open: true }),
  hide: () => set({ open: false }),
}))
