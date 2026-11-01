import { forwardRef, useImperativeHandle, useRef, type CSSProperties, type KeyboardEvent, type ReactNode } from 'react'
import { FocusContext, useFocusable } from '@noriginmedia/norigin-spatial-navigation-react'
import type { FocusableComponentLayout } from '@noriginmedia/norigin-spatial-navigation-core'
import { cn } from '../lib/util'

export interface FocusableProps {
  focusKey?: string
  className?: string
  style?: CSSProperties
  children: ReactNode | ((s: { focused: boolean }) => ReactNode)
  onEnter?: () => void
  onFocus?: (layout: FocusableComponentLayout) => void
  onBlur?: () => void
  onArrow?: (direction: string) => boolean
  label?: string
  role?: string
  disabled?: boolean
  /** scroll the element into view when it receives focus (default true) */
  scroll?: boolean | ScrollLogicalPosition
  'aria-pressed'?: boolean
  'aria-current'?: 'page' | boolean
  'data-testid'?: string
  title?: string
  forceFocus?: boolean
  data?: Record<string, string | boolean | undefined>
}

export interface FocusableHandle { focus: () => void; el: HTMLElement | null }

/** A single focusable control. Click, Enter and D-pad OK all route to onEnter. Mouse hover moves focus too. */
export const Focusable = forwardRef<FocusableHandle, FocusableProps>(function Focusable(p, outer) {
  const onEnterRef = useRef(p.onEnter)
  onEnterRef.current = p.onEnter
  const { ref, focused, focusSelf } = useFocusable({
    focusKey: p.focusKey,
    focusable: !p.disabled,
    autoRestoreFocus: false,
    forceFocus: p.forceFocus,
    onEnterPress: () => onEnterRef.current?.(),
    onFocus: (layout) => {
      if (p.scroll !== false) {
        ;(ref.current as HTMLElement | null)?.scrollIntoView?.({
          block: p.scroll === true || p.scroll === undefined ? 'nearest' : p.scroll,
          inline: 'nearest',
          behavior: 'auto',
        })
      }
      p.onFocus?.(layout)
    },
    onBlur: () => p.onBlur?.(),
    onArrowPress: p.onArrow ? (d) => p.onArrow!(d) : undefined,
    accessibilityLabel: p.label,
  })
  useImperativeHandle(outer, () => ({ focus: focusSelf, el: ref.current as HTMLElement | null }), [focusSelf, ref])
  return (
    <div
      ref={ref}
      role={p.role ?? 'button'}
      tabIndex={-1}
      aria-label={p.label}
      aria-disabled={p.disabled || undefined}
      aria-pressed={p['aria-pressed']}
      aria-current={p['aria-current']}
      data-testid={p['data-testid']}
      title={p.title}
      data-focused={focused}
      {...Object.fromEntries(Object.entries(p.data ?? {}).map(([k, v]) => [`data-${k}`, v]))}
      className={cn('fx', p.className)}
      style={p.style}
      onClick={() => !p.disabled && onEnterRef.current?.()}
      onPointerEnter={(e) => e.pointerType === 'mouse' && !p.disabled && focusSelf()}
    >
      {typeof p.children === 'function' ? p.children({ focused }) : p.children}
    </div>
  )
})

export interface FocusGroupProps {
  focusKey?: string
  className?: string
  style?: CSSProperties
  children: ReactNode
  /** remember the last focused child and restore it when focus re-enters */
  remember?: boolean
  preferred?: string
  boundary?: boolean
  onFocusChange?: (hasFocus: boolean) => void
  role?: string
  label?: string
  'data-testid'?: string
  as?: 'div' | 'section' | 'nav' | 'main' | 'ul'
  onArrow?: (direction: string) => boolean
  data?: Record<string, string | boolean | undefined>
}

/** A container: provides a FocusContext so descendant Focusables navigate within/around it. */
export function FocusGroup(p: FocusGroupProps) {
  const { ref, focusKey, hasFocusedChild } = useFocusable({
    focusKey: p.focusKey,
    saveLastFocusedChild: p.remember ?? true,
    autoRestoreFocus: false,
    trackChildren: true,
    preferredChildFocusKey: p.preferred,
    isFocusBoundary: p.boundary,
    onArrowPress: p.onArrow ? (d) => p.onArrow!(d) : undefined,
  })
  const prev = useRef(false)
  if (prev.current !== hasFocusedChild) {
    prev.current = hasFocusedChild
    queueMicrotask(() => p.onFocusChange?.(hasFocusedChild))
  }
  const Tag = (p.as ?? 'div') as 'div'
  const dataAttrs = Object.fromEntries(Object.entries(p.data ?? {}).map(([k, v]) => [`data-${k}`, v]))
  return (
    <FocusContext.Provider value={focusKey}>
      <Tag ref={ref} role={p.role} aria-label={p.label} data-testid={p['data-testid']} className={p.className} style={p.style} {...dataAttrs}>
        {p.children}
      </Tag>
    </FocusContext.Provider>
  )
}

/** Text input that takes DOM focus when spatially focused; Left/Right move the caret unless at an edge. */
export function FocusableInput(p: {
  focusKey?: string
  value: string
  onChange: (v: string) => void
  onSubmit?: () => void
  placeholder?: string
  label: string
  type?: string
  className?: string
  inputMode?: 'text' | 'numeric' | 'search' | 'url'
  maxLength?: number
  autoCapitalize?: string
  autoComplete?: string
  readOnly?: boolean
  inputRef?: React.RefObject<HTMLInputElement | null>
  onFocused?: () => void
}) {
  const local = useRef<HTMLInputElement>(null)
  const inputRef = p.inputRef ?? local
  const { ref, focused } = useFocusable({
    focusKey: p.focusKey,
    onFocus: () => {
      inputRef.current?.focus({ preventScroll: true })
      ;(ref.current as HTMLElement | null)?.scrollIntoView?.({ block: 'nearest' })
      p.onFocused?.()
    },
    onBlur: () => inputRef.current?.blur(),
  })
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    const el = e.currentTarget
    const atStart = el.selectionStart === 0 && el.selectionEnd === 0
    const atEnd = el.selectionStart === el.value.length && el.selectionEnd === el.value.length
    if ((e.key === 'ArrowLeft' && !atStart) || (e.key === 'ArrowRight' && !atEnd)) e.nativeEvent.stopPropagation()
    if (e.key === 'Enter') {
      e.nativeEvent.stopPropagation()
      p.onSubmit?.()
    }
    if (e.key === 'Backspace') e.nativeEvent.stopPropagation()
  }
  return (
    <div ref={ref} data-focused={focused} className={cn('fx field', p.className)}>
      <input
        ref={inputRef}
        tabIndex={-1}
        aria-label={p.label}
        type={p.type ?? 'text'}
        inputMode={p.inputMode}
        maxLength={p.maxLength}
        autoCapitalize={p.autoCapitalize}
        autoComplete={p.autoComplete ?? "off"}
        readOnly={p.readOnly}
        spellCheck={false}
        placeholder={p.placeholder}
        value={p.value}
        onChange={(e) => p.onChange(e.target.value)}
        onKeyDown={onKeyDown}
      />
    </div>
  )
}
