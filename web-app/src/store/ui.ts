import { create } from 'zustand'
import type { Channel } from '../api/types'
import { lsGet, lsSet, safeJson } from '../lib/util'

export interface Toast {
  id: number
  text: string
  tone?: 'info' | 'error' | 'success'
}

interface HomePrefs {
  order: string[] // row ids/titles in preferred order
  hidden: string[]
}

interface UiState {
  railPinned: boolean
  setRailPinned: (v: boolean) => void
  toasts: Toast[]
  toast: (text: string, tone?: Toast['tone']) => void
  dismissToast: (id: number) => void
  recentSearches: string[]
  /** Current Search text, so Back from a result returns to the same query, results and focused result. */
  searchQuery: string
  setSearchQuery: (q: string) => void
  addSearch: (q: string) => void
  clearSearches: () => void
  homePrefs: HomePrefs
  setHomePrefs: (p: HomePrefs) => void
  /** Last-loaded ordered channel list, used for zapping in the player (contract has no list_id lookup). */
  channelList: { listId: string | null; channels: Channel[] }
  setChannelList: (listId: string | null, channels: Channel[]) => void
  lastChannelId: string | null
  setLastChannelId: (id: string | null) => void
  sleepMinutes: number | null
  setSleepMinutes: (m: number | null) => void
}

let toastSeq = 0
const HOME_KEY = 'sh.homePrefs.v1'
const SEARCH_KEY = 'sh.recentSearches.v1'
const QUERY_KEY = 'sh.searchQuery.v1'

// sessionStorage can be missing or throw (private mode, blocked storage); the store works without it
function ssGet(k: string): string | null {
  try { return sessionStorage.getItem(k) } catch { return null }
}
function ssSet(k: string, v: string | null): void {
  try { if (v === null) sessionStorage.removeItem(k); else sessionStorage.setItem(k, v) } catch { /* ignore */ }
}

export const useUi = create<UiState>((set, get) => ({
  railPinned: false,
  setRailPinned: (railPinned) => set({ railPinned }),
  toasts: [],
  toast: (text, tone = 'info') => {
    const id = ++toastSeq
    set({ toasts: [...get().toasts, { id, text, tone }] })
    window.setTimeout(() => get().dismissToast(id), 5000)
  },
  dismissToast: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),
  recentSearches: safeJson<string[]>(lsGet(SEARCH_KEY), []),
  searchQuery: ssGet(QUERY_KEY) ?? '',
  setSearchQuery: (searchQuery) => {
    set({ searchQuery })
    ssSet(QUERY_KEY, searchQuery || null)
  },
  addSearch: (q) => {
    const next = [q, ...get().recentSearches.filter((s) => s !== q)].slice(0, 8)
    set({ recentSearches: next })
    lsSet(SEARCH_KEY, JSON.stringify(next))
  },
  clearSearches: () => {
    set({ recentSearches: [] })
    lsSet(SEARCH_KEY, null)
  },
  homePrefs: safeJson<HomePrefs>(lsGet(HOME_KEY), { order: [], hidden: [] }),
  setHomePrefs: (homePrefs) => {
    set({ homePrefs })
    lsSet(HOME_KEY, JSON.stringify(homePrefs))
  },
  channelList: { listId: null, channels: [] },
  setChannelList: (listId, channels) => set({ channelList: { listId, channels } }),
  lastChannelId: null,
  setLastChannelId: (lastChannelId) => set({ lastChannelId }),
  sleepMinutes: null,
  setSleepMinutes: (sleepMinutes) => set({ sleepMinutes }),
}))
