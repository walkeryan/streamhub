import { create } from 'zustand'
import { bridgeOf, MAX_VOICE_TEXT, offersVoice, voiceCommand, type VoiceError, type VoiceEvent } from '../shellProtocol'

/*
 * Voice search: the words a person says go into the search box and are searched for. Two recognisers,
 * never both: inside the Android TV app the TV's own (asked over the bridge, see shellProtocol.ts: it
 * listens through the remote's microphone and reports `voice` messages), and in a browser the Web Speech
 * API where it exists (Chrome, Edge, Android Chrome). Elsewhere there is no microphone button at all.
 */

/* The few parts of the Web Speech API used here (not in every TypeScript DOM library). */
interface SpeechAlternative { transcript: string }
interface SpeechResult { readonly length: number; isFinal: boolean; [index: number]: SpeechAlternative }
interface SpeechResultList { readonly length: number; [index: number]: SpeechResult }
interface Recognition {
  lang: string; interimResults: boolean; continuous: boolean; maxAlternatives: number
  onresult: ((e: { results: SpeechResultList }) => void) | null
  onerror: ((e: { error: string }) => void) | null
  onend: (() => void) | null
  start(): void; stop(): void; abort(): void
}
type RecognitionCtor = new () => Recognition

/** The browser's speech recogniser, or null where there is none. */
export function webRecognizer(w: unknown = typeof window === 'undefined' ? undefined : window): RecognitionCtor | null {
  const o = w as { SpeechRecognition?: unknown; webkitSpeechRecognition?: unknown } | undefined
  const ctor = o?.SpeechRecognition ?? o?.webkitSpeechRecognition
  return typeof ctor === 'function' ? (ctor as RecognitionCtor) : null
}

export type VoiceSource = 'shell' | 'web'

/** Where voice search would come from on this page, or null when it is not offered. */
export function voiceSource(w: unknown = typeof window === 'undefined' ? undefined : window): VoiceSource | null {
  if (!w) return null
  const bridge = bridgeOf(w)
  // In the TV app only the TV listens: a WebView's own Web Speech API does not reach the remote.
  if (bridge) {
    if (typeof bridge.message !== 'function') return null
    try { return offersVoice(bridge.capabilities()) ? 'shell' : null } catch { return null }
  }
  return webRecognizer(w) ? 'web' : null
}

/** Older TV shells open an external activity. Only an embedded recognizer may auto-start. */
export function inlineVoice(): boolean {
  const source = voiceSource()
  if (source !== 'shell') return source === 'web'
  try { return JSON.parse(bridgeOf(window)!.capabilities()).inline_voice_search === true } catch { return false }
}

/** The words of a browser recogniser's results so far, and whether they are final. */
export function heardOf(results: SpeechResultList): { text: string; final: boolean } {
  let text = ''
  let final = results.length > 0
  for (let i = 0; i < results.length; i++) {
    const r = results[i]!
    text += r[0]?.transcript ?? ''
    if (!r.isFinal) final = false
  }
  return { text: text.replace(/\s+/g, ' ').trim().slice(0, MAX_VOICE_TEXT), final }
}

/** A browser recogniser's error as one of the reasons the TV gives too; null when it was only stopped. */
export function webVoiceError(error: string): VoiceError | null {
  switch (error) {
    case 'aborted': return null
    case 'no-speech': case 'no-match': return 'no_match'
    case 'not-allowed': case 'service-not-allowed': return 'permission'
    case 'network': return 'network'
    case 'audio-capture': case 'language-not-supported': return 'unavailable'
    default: return 'other'
  }
}

export type VoiceStatus = 'idle' | 'listening'

interface VoiceStore {
  status: VoiceStatus
  /** The words so far while listening, shown in the box before anything is searched for. */
  heard: string
  /** What was said, numbered so each is searched for once. */
  final: { text: string; seq: number } | null
  error: { code: VoiceError; seq: number } | null
  start: () => void
  stop: () => void
  /** What the recogniser reported (the TV's `voice` messages arrive here, see shell.ts). */
  receive: (e: VoiceEvent) => void
}

let recognition: Recognition | null = null
let seq = 0

export const useVoice = create<VoiceStore>((set, get) => ({
  status: 'idle',
  heard: '',
  final: null,
  error: null,
  receive: (e) => {
    if (e.state === 'listening') set({ status: 'listening', heard: get().status === 'listening' ? get().heard : '' })
    else if (e.state === 'partial') set({ status: 'listening', heard: e.text ?? '' })
    else if (e.state === 'final' && e.text) set({ status: 'idle', heard: '', final: { text: e.text, seq: ++seq } })
    else if (e.state === 'error') set({ status: 'idle', heard: '', error: { code: e.error ?? 'other', seq: ++seq } })
    else set({ status: 'idle', heard: '' })
  },
  start: () => {
    if (get().status === 'listening') return
    const source = voiceSource()
    const { receive } = get()
    if (source === 'shell') {
      const bridge = bridgeOf(window)
      let taken: boolean
      try { taken = bridge?.message?.(voiceCommand('start')) === true } catch { taken = false }
      if (taken) receive({ state: 'listening', text: null, error: null })
      else receive({ state: 'error', text: null, error: 'unavailable' })
      return
    }
    const Ctor = webRecognizer()
    if (!Ctor) { receive({ state: 'error', text: null, error: 'unavailable' }); return }
    recognition?.abort()
    const r = new Ctor()
    recognition = r
    r.lang = document.documentElement.lang || navigator.language || 'en-US'
    r.interimResults = true
    r.continuous = false
    r.maxAlternatives = 1
    r.onresult = (e) => {
      if (recognition !== r) return
      const { text, final } = heardOf(e.results)
      if (final && text) { recognition = null; receive({ state: 'final', text, error: null }) }
      else if (text) receive({ state: 'partial', text, error: null })
    }
    r.onerror = (e) => {
      if (recognition !== r) return
      recognition = null
      const code = webVoiceError(e.error)
      receive(code ? { state: 'error', text: null, error: code } : { state: 'idle', text: null, error: null })
    }
    // Stopped without a final result (silence, or the browser gave up): back to the box as it was.
    r.onend = () => { if (recognition !== r) return; recognition = null; receive({ state: 'idle', text: null, error: null }) }
    try { r.start(); receive({ state: 'listening', text: null, error: null }) } catch { recognition = null; receive({ state: 'error', text: null, error: 'busy' }) }
  },
  stop: () => {
    if (get().status !== 'listening') return
    if (voiceSource() === 'shell') { try { bridgeOf(window)?.message?.(voiceCommand('stop')) } catch { /* the TV stops on its own */ } }
    const r = recognition
    recognition = null
    r?.abort()
    set({ status: 'idle', heard: '' })
  },
}))
