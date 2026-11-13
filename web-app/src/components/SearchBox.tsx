import { useRef, useState, type KeyboardEvent } from 'react'
import { useFocusable } from '@noriginmedia/norigin-spatial-navigation-react'
import { Icon } from './Icon'
import { setFocus } from '../nav/spatial'
import { SEARCH_BOX, useSearchQuery, useSlashToSearch } from '../lib/searchQuery'

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
    <div ref={ref} className="rail-search fx" role="search" data-testid="nav-search" data-focus-key={focusKey} data-focused={focused}
      data-open={typing || undefined} data-filled={box.text ? true : undefined} aria-current={box.onSearch ? 'page' : undefined}
      onClick={() => { focusSelf(); caret() }}>
      <Icon name="search" />
      <input ref={input} type="text" inputMode="search" enterKeyHint="search" tabIndex={-1} aria-label={label} placeholder={label} autoComplete="off" spellCheck={false}
        value={box.text} onChange={(e) => box.setText(e.target.value)} onKeyDown={onKeyDown}
        onFocus={() => { setTyping(true); if (!focused) focusSelf() }} onBlur={() => setTyping(false)} />
      {box.text && <button type="button" className="rail-search-clear" tabIndex={-1} aria-label="Clear search"
        onMouseDown={(e) => e.preventDefault()} onClick={(e) => { e.stopPropagation(); box.clear(); caret() }}><Icon name="x" size={16} /></button>}
    </div>
  )
}
