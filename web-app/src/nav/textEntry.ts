import { useCallback, useEffect, useState, type RefObject } from 'react'
import { flushSync } from 'react-dom'
import { inShell } from '../shell'
import { IME_HIDDEN_EVENT } from '../shellProtocol'
import { useBackHandler } from './back'

type InputMode = 'text' | 'numeric' | 'search' | 'url' | 'none'

/**
 * When a text field takes the keys. In a browser that is whenever it has focus, as ever. On the TV (the
 * Android TV app) the remote's arrows only pass over a field: it is a stop like a button, read-only with
 * no on-screen keyboard (`inputMode="none"`), until OK (or a pointer's click) starts editing, which opens
 * the keyboard. Back, the keyboard closing, or leaving the field ends editing; focus stays on the field.
 *
 * `inputProps` go on the `<input>`; `start` is what OK on the field does, `stop` ends editing.
 */
export function useTextEntry(input: RefObject<HTMLInputElement | null>, opts: { readOnly?: boolean; inputMode?: Exclude<InputMode, 'none'> } = {}) {
  const shell = inShell()
  const [editing, setEditing] = useState(false)
  const start = useCallback(() => {
    const el = input.current
    if (!el) return
    if (shell) {
      // Editable before it takes focus: the keyboard opens for an editable field, never for a read-only one
      // (a click may have focused it read-only already: it is focused afresh).
      if (document.activeElement === el) el.blur()
      flushSync(() => setEditing(true))
    }
    el.focus({ preventScroll: true })
    const end = el.value.length
    try { el.setSelectionRange(end, end) } catch { /* not every type has a caret */ }
  }, [input, shell])
  const stop = useCallback(() => {
    setEditing(false)
    if (document.activeElement === input.current) input.current?.blur()
  }, [input])
  // Back while editing on the TV ends editing (the keyboard closes); the next Back is the screen's.
  useBackHandler(() => { stop(); return true }, shell && editing)
  useEffect(() => {
    if (!shell || !editing) return
    window.addEventListener(IME_HIDDEN_EVENT, stop)
    return () => window.removeEventListener(IME_HIDDEN_EVENT, stop)
  }, [shell, editing, stop])
  const locked = shell && !editing
  return {
    shell, editing, start, stop,
    inputProps: {
      readOnly: opts.readOnly || locked,
      inputMode: (locked ? 'none' : opts.inputMode) as InputMode | undefined,
      onBlur: () => setEditing(false),
      // A click on the field means to type, on the TV too.
      onClick: locked ? () => start() : undefined,
    },
  }
}
