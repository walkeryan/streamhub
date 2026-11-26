import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { flushSync } from 'react-dom'
import { useLocation } from 'react-router'
import { useTextEntry } from '../nav/textEntry'
import { useFocusable } from '@noriginmedia/norigin-spatial-navigation-react'
import { Icon } from './Icon'
import { Focusable } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { useBackHandler } from '../nav/back'
import { SEARCH_BOX, useSearchQuery, useSlashToSearch } from '../lib/searchQuery'
import { inlineVoice, useVoice } from '../lib/voiceSearch'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'

const VOICE_STOPPED = 'Voice search stopped. Try again.'
const VOICE_ERRORS: Record<string, string> = {
  no_match: "Didn't catch that. Try again.",
  permission: 'Voice search needs the microphone. Allow it and try again.',
  unavailable: 'Voice search is not available here.',
  network: 'Voice search needs a connection. Try again.',
  busy: 'Voice search is busy. Try again.',
  other: 'Voice search stopped. Try again.',
}

/** Activation starts a single voice query. Moving through the bar and the / shortcut keep the mic off. */
export function SearchBox({ label, page = false }: { label: string; page?: boolean }) {
  const box = useSearchQuery()
  const location = useLocation()
  const input = useRef<HTMLInputElement>(null)
  const entry = useTextEntry(input, { inputMode: 'search' })
  const [typing, setTyping] = useState(false)
  const voice = useVoice()
  const canVoice = inlineVoice()
  const listening = voice.status === 'listening'
  const key = page ? SEARCH_BOX.page : SEARCH_BOX.bar
  const voiceAttempted = useRef(false)
  const typeQuery = () => {
    voiceAttempted.current = true
    if (voice.status === 'listening') flushSync(() => voice.stop())
    if (input.current?.readOnly || document.activeElement !== input.current) entry.start()
  }
  const activate = () => {
    if (!box.onSearch) box.submit()
    if (canVoice && !voiceAttempted.current && !listening && !entry.editing) {
      voiceAttempted.current = true
      input.current?.focus({ preventScroll: true })
      voice.start()
    } else typeQuery()
  }
  const { ref, focused, focusKey, focusSelf } = useFocusable({
    focusKey: key,
    onEnterPress: activate,
    onFocus: (_layout, _props, details) => {
      if (entry.shell || !(details?.event instanceof window.KeyboardEvent && details.event.key.startsWith('Arrow'))) input.current?.focus({ preventScroll: true })
    },
    onBlur: entry.stop,
  })
  useSlashToSearch(() => { void setFocus(key); typeQuery() })
  useBackHandler(() => { if (listening) voice.stop(); else box.clear(); return true }, listening || (focused && !!box.text))
  useEffect(() => {
    if (!box.onSearch) {
      voiceAttempted.current = false
      useVoice.getState().stop()
    }
  }, [box.onSearch, location.pathname])
  const profileId = useSession(s => s.profile?.id)
  useEffect(() => () => useVoice.getState().stop(), [profileId])
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
      toast(VOICE_ERRORS[voice.error.code] ?? VOICE_STOPPED, 'error')
    }
  }, [voice.error, toast])
  const clear = () => { voice.stop(); box.clear(); void setFocus(key) }
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    const el = e.currentTarget
    // A real keyboard can type immediately while the remote microphone owns the field.
    // Unlock it before the browser inserts the first character, including an IME composition.
    if ((e.key.length === 1 || e.key === 'Process' || e.key === 'Dead') && !e.altKey && !e.ctrlKey && !e.metaKey &&
        (listening || (entry.shell && el.readOnly))) typeQuery()
    const atStart = el.selectionStart === 0 && el.selectionEnd === 0
    const atEnd = el.selectionStart === el.value.length && el.selectionEnd === el.value.length
    // Inside the text Left and Right move the caret; at its ends they move along the bar.
    if ((e.key === 'ArrowLeft' && !atStart) || (e.key === 'ArrowRight' && !atEnd) || e.key === 'Backspace') e.nativeEvent.stopPropagation()
    if (e.key === 'Enter') {
      e.nativeEvent.stopPropagation()
      if (e.nativeEvent.isComposing) return
      if (entry.shell && !entry.editing) activate()
      else { if (entry.editing) entry.stop(); box.submit() }
    }
    if (e.key === 'Escape') {
      // Handled here: the page's Back must not also run.
      e.preventDefault()
      if (listening) voice.stop()
      else if (box.text) box.clear()
      else { el.blur(); if (box.onSearch) box.leave(); else void setFocus('MAIN') }
    }
  }
  return (
    <>
    <div ref={ref} className={page ? "field search-mobile-input fx" : "rail-search fx"} role="search" data-testid={page ? "search-page-input" : "nav-search"} data-text-entry={entry.shell && box.onSearch ? (entry.editing ? "editing" : "idle") : undefined} data-focus-key={focusKey} data-focused={focused}
      data-open={typing || listening || undefined} data-listening={listening || undefined} data-filled={box.text ? true : undefined} aria-current={box.onSearch ? 'page' : undefined}
      onClick={() => { focusSelf(); activate() }}>
      <Icon name={listening ? "mic" : "search"} />
      <input ref={input} type="text" inputMode={entry.inputProps.inputMode} readOnly={entry.inputProps.readOnly} enterKeyHint="search" tabIndex={-1} aria-label={label} autoComplete="off" spellCheck={false}
        value={listening ? voice.heard : box.text} placeholder={listening ? 'Listening…' : label}
        onChange={(e) => { voice.stop(); box.setText(e.target.value) }} onKeyDown={onKeyDown}
        onFocus={() => { setTyping(true); if (!focused) focusSelf() }} onBlur={() => { setTyping(false); entry.inputProps.onBlur() }} />
      {/* Clearing is the stop beside the box (below), for the remote and the mouse alike. */}
    </div>
    {/* The remote's stops beside the box: clear what is typed, and speak instead of typing. */}
    {box.text && <Focusable focusKey={SEARCH_BOX.clear} className="rail-item rail-icon fx-flat rail-search-action" label="Clear search" title="Clear search"
      data-testid={page ? "search-page-clear" : "nav-search-clear"} onEnter={clear}><Icon name="x" /></Focusable>}
    {canVoice && <Focusable focusKey={SEARCH_BOX.voice} className="rail-item rail-icon fx-flat rail-search-action" label={listening ? 'Stop listening' : 'Search by voice'}
      title={listening ? 'Stop listening' : 'Search by voice'} aria-pressed={listening} data-listening={listening || undefined} data-testid={page ? "search-page-voice" : "nav-search-voice"}
      onEnter={() => { if (listening) voice.stop(); else { box.submit(); voice.start() } }}><Icon name="mic" /></Focusable>}
    {entry.shell && <Focusable focusKey="search:type" className="rail-item fx-flat" label="Type search" onEnter={() => { if (!box.onSearch) box.submit(); focusSelf(); typeQuery() }}>Type</Focusable>}
    <span className="sr-only" role="status">{listening ? 'Listening…' : entry.editing || (typing && !entry.shell) ? 'Typing' : ''}</span>
    </>
  )
}
