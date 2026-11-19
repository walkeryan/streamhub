import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useFocusable } from '@noriginmedia/norigin-spatial-navigation-react'
import { Icon } from './Icon'
import { Focusable } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { useBackHandler } from '../nav/back'
import { SEARCH_BOX, useSearchQuery, useSlashToSearch } from '../lib/searchQuery'
import { useVoice, voiceSource } from '../lib/voiceSearch'
import { useUi } from '../store/ui'

const VOICE_ERRORS: Record<string, string> = {
  no_match: "Didn't catch that. Try again.",
  permission: 'Voice search needs the microphone. Allow it and try again.',
  unavailable: 'Voice search is not available here.',
  network: 'Voice search needs a connection. Try again.',
  busy: 'Voice search is busy. Try again.',
  other: 'Voice search stopped. Try again.',
}

const ARROWS = new Set(['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'])

/**
 * The search box of the top bar: the magnifier with a short field beside it. Typing asks (see
 * `useSearchQuery`) and the caret stays here; Escape clears, then leaves search. It is one stop of the
 * bar for a remote: arrows land on it without taking the caret, so the on-screen keyboard opens only
 * when the viewer presses OK on it. Tab, "/" and a click mean to type, and put the caret in at once.
 * On a narrow bar only the magnifier shows until the box is in use, when it opens over the bar.
 */
export function SearchBox({ label }: { label: string }) {
  const box = useSearchQuery()
  const input = useRef<HTMLInputElement>(null)
  const [typing, setTyping] = useState(false)
  const caret = () => input.current?.focus({ preventScroll: true })
  const { ref, focused, focusKey, focusSelf } = useFocusable({
    focusKey: SEARCH_BOX.bar,
    onEnterPress: caret,
    onFocus: (_layout, _props, details) => { if (!(details?.event instanceof KeyboardEvent && ARROWS.has(details.event.key))) caret() },
    onBlur: () => input.current?.blur(),
  })
  useSlashToSearch(() => { void setFocus(SEARCH_BOX.bar) })
  // Back (the remote's, which never reaches the field as Escape) clears what is typed first, then leaves.
  useBackHandler(() => { box.clear(); return true }, focused && !!box.text)
  // Voice: the words so far show in the box; what was said is searched for.
  const voice = useVoice()
  const canVoice = voiceSource() !== null
  const toast = useUi((s) => s.toast)
  const seenFinal = useRef(voice.final?.seq ?? 0)
  const seenError = useRef(voice.error?.seq ?? 0)
  useEffect(() => {
    if (voice.final && voice.final.seq !== seenFinal.current) {
      seenFinal.current = voice.final.seq
      box.setText(voice.final.text)
      box.submit(voice.final.text)
    }
  }, [voice.final, box])
  useEffect(() => {
    if (voice.error && voice.error.seq !== seenError.current) {
      seenError.current = voice.error.seq
      toast(VOICE_ERRORS[voice.error.code] ?? VOICE_ERRORS.other, 'error')
    }
  }, [voice.error, toast])
  const listening = voice.status === 'listening'
  const clear = () => { box.clear(); void setFocus(SEARCH_BOX.bar) }
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    const el = e.currentTarget
    const atStart = el.selectionStart === 0 && el.selectionEnd === 0
    const atEnd = el.selectionStart === el.value.length && el.selectionEnd === el.value.length
    // Inside the text Left and Right move the caret; at its ends they move along the bar.
    if ((e.key === 'ArrowLeft' && !atStart) || (e.key === 'ArrowRight' && !atEnd) || e.key === 'Backspace') e.nativeEvent.stopPropagation()
    if (e.key === 'Enter') { e.nativeEvent.stopPropagation(); box.submit() }
    if (e.key === 'Escape') {
      // Handled here: the page's Back must not also run.
      e.preventDefault()
      if (box.text) box.clear()
      else { el.blur(); if (box.onSearch) box.leave(); else void setFocus('MAIN') }
    }
  }
  return (
    <>
    <div ref={ref} className="rail-search fx" role="search" data-testid="nav-search" data-focus-key={focusKey} data-focused={focused}
      data-open={typing || undefined} data-filled={box.text ? true : undefined} aria-current={box.onSearch ? 'page' : undefined}
      onClick={() => { focusSelf(); caret() }}>
      <Icon name="search" />
      <input ref={input} type="text" inputMode="search" enterKeyHint="search" tabIndex={-1} aria-label={label} autoComplete="off" spellCheck={false}
        value={listening && voice.heard ? voice.heard : box.text} placeholder={listening ? 'Listening…' : label}
        onChange={(e) => box.setText(e.target.value)} onKeyDown={onKeyDown}
        onFocus={() => { setTyping(true); if (!focused) focusSelf() }} onBlur={() => setTyping(false)} />
      {/* Clearing is the stop beside the box (below), for the remote and the mouse alike. */}
    </div>
    {/* The remote's stops beside the box: clear what is typed, and speak instead of typing. */}
    {box.text && <Focusable focusKey={SEARCH_BOX.clear} className="rail-item rail-icon fx-flat rail-search-action" label="Clear search" title="Clear search"
      data-testid="nav-search-clear" onEnter={clear}><Icon name="x" /></Focusable>}
    {canVoice && <Focusable focusKey={SEARCH_BOX.voice} className="rail-item rail-icon fx-flat rail-search-action" label={listening ? 'Stop listening' : 'Search by voice'}
      title={listening ? 'Stop listening' : 'Search by voice'} aria-pressed={listening} data-listening={listening || undefined} data-testid="nav-search-voice"
      onEnter={() => (listening ? voice.stop() : voice.start())}><Icon name="mic" /></Focusable>}
    </>
  )
}
