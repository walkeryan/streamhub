import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router'

export const SEARCH_PATH = '/search'
/** The one search control: the field in the top bar, or the field on the results page of a phone. */
export const SEARCH_BOX = { bar: 'rail:/search', page: 'search:input' } as const
/** Typing waits this long before it asks; Enter asks at once. */
const DEBOUNCE_MS = 250
/** Shorter than this the server looks nothing up, so neither does the page. */
export const MIN_QUERY = 2

export const searchPath = (q: string) => (q.trim() ? `${SEARCH_PATH}?q=${encodeURIComponent(q.trim())}` : SEARCH_PATH)

/**
 * The text of the search box and the address it drives. The address is the query: typing navigates to
 * /search?q=…, replacing the entry while the viewer is already on the results so Back leaves search in
 * one step; arriving on other results (Back, a link) or leaving search puts that query, or none, in the box.
 */
export function useSearchQuery() {
  const loc = useLocation()
  const navigate = useNavigate()
  const onSearch = loc.pathname === SEARCH_PATH
  const urlQuery = onSearch ? (new URLSearchParams(loc.search).get('q') ?? '') : ''
  const [text, setText] = useState(urlQuery)
  // What the address holds because of this box. Anything else there came from another navigation.
  const sent = useRef(urlQuery)
  useEffect(() => {
    if (urlQuery === sent.current) return
    sent.current = urlQuery
    setText(urlQuery)
  }, [urlQuery])
  const ask = useCallback((value: string, always = false) => {
    const q = value.trim()
    if (onSearch ? q === sent.current : !q && !always) return
    sent.current = q
    navigate(searchPath(q), { replace: onSearch })
  }, [navigate, onSearch])
  useEffect(() => {
    if (text.trim() === sent.current) return
    const id = window.setTimeout(() => ask(text), DEBOUNCE_MS)
    return () => window.clearTimeout(id)
  }, [text, ask])
  const atRoot = loc.key === 'default'
  return {
    text, setText, onSearch,
    /** Enter: ask now; with nothing typed, open the search page. */
    submit: () => ask(text, true),
    clear: () => { setText(''); ask('') },
    /** Back out of search to where the viewer was. */
    leave: () => { if (!onSearch) return; if (atRoot) navigate('/', { replace: true }); else navigate(-1) },
  }
}

/** "/" puts the caret in the search box from anywhere but another field or an open dialog. */
export function useSlashToSearch(focusBox: () => void): void {
  const run = useRef(focusBox)
  run.current = focusBox
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented) return
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return
      if (document.querySelector('.scrim')) return
      e.preventDefault()
      run.current()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
}
