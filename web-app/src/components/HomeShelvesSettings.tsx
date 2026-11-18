import { useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, ok } from '../api/client'
import { qk, useBrowseShelves, useSettings } from '../api/hooks'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { useUi } from '../store/ui'
import { Button, Chip, ErrorState, Modal } from './ui'
import { GlassIconButton } from '../ds/GlassIconButton'
import { GlassPanel } from '../ds/States'
import { SectionHeader } from '../ds/Section'

/** The server keeps at most this many entries and shows the first twelve. */
const MAX_ENTRIES = 24, SHOWN = 12, MAX_LENGTH = 200
type Side = 'movies' | 'shows'
const SIDE_NAME: Record<Side, string> = { movies: 'Movies', shows: 'Shows' }
/** An entry is a shelf name, or movies:Name / shows:Name for one side only. */
const parse = (entry: string): { side: Side | null; name: string } => {
  const sided = /^(movies|shows):(.+)$/.exec(entry)
  return sided ? { side: sided[1] as Side, name: sided[2]! } : { side: null, name: entry }
}

/**
 * Which shelves Home shows after New Releases, for everyone, and in what order: the server's
 * `home_shelves` setting, for holders of system.manage. Every change is saved at once. An entry that
 * names no shelf right now (`home_shelves_unknown`) is kept and marked: the shelf may come with a
 * later sync. An empty list is Automatic: Home chooses.
 */
export function HomeShelvesSettings() {
  const settings = useSettings()
  const movies = useBrowseShelves('movies', { limit_rows: 40, limit_items: 1 })
  const shows = useBrowseShelves('shows', { limit_rows: 40, limit_items: 1 })
  const qc = useQueryClient()
  const toast = useUi((s) => s.toast)
  // What was just saved, shown until the server's own copy has been read again. Changes made in quick
  // succession are sent one after another, each built on the one before.
  const [saved, setSaved] = useState<string[] | null>(null)
  const queue = useRef<Promise<void>>(Promise.resolve())
  const waiting = useRef(0)
  const [name, setName] = useState('')
  const [clearing, setClearing] = useState(false)
  if (settings.error) return <ErrorState error={settings.error} onRetry={() => void settings.refetch()} />
  if (!settings.data) return <p className="acct-note">Loading…</p>

  const list = saved ?? settings.data.home_shelves ?? []
  const unknown = new Set(saved ? [] : settings.data.home_shelves_unknown ?? [])
  const names: Record<Side, string[]> = { movies: (movies.data?.shelves ?? []).map((s) => s.name), shows: (shows.data?.shelves ?? []).map((s) => s.name) }
  const onBoth = (shelf: string) => names.movies.includes(shelf) && names.shows.includes(shelf)
  // A shelf both sides have is offered once per side; a name only one side has needs no prefix.
  const entryFor = (side: Side, shelf: string) => (onBoth(shelf) ? `${side}:${shelf}` : shelf)
  const offered = (side: Side) => names[side].filter((shelf) => shelf !== 'New Releases' && !list.includes(shelf) && !list.includes(`${side}:${shelf}`))
  const where = (entry: string): string => {
    const { side, name: shelf } = parse(entry)
    if (side) return `${SIDE_NAME[side]} only`
    return onBoth(shelf) ? 'Movies and shows' : names.movies.includes(shelf) ? 'Movies' : names.shows.includes(shelf) ? 'Shows' : ''
  }

  // The list itself shows what changed; only a failure, and the reset, are worth a notice.
  const save = (next: string[], focus?: string, said?: string) => {
    setSaved(next)
    if (focus) void setFocus(focus)
    waiting.current++
    queue.current = queue.current.then(async () => {
      try {
        await ok(api.PATCH('/settings', { body: { home_shelves: next } }))
        if (said) toast(said, 'success')
      } catch (e) { toast(e instanceof Error ? e.message : 'Could not save.', 'error') }
      if (--waiting.current > 0) return
      // Home is built from this list; the setting comes back with what the server kept and which names it does not know.
      await Promise.all([qc.invalidateQueries({ queryKey: qk.settings }), qc.invalidateQueries({ queryKey: qk.home })])
      if (waiting.current === 0) setSaved(null)
    })
  }
  const move = (i: number, d: number) => {
    const j = i + d
    if (j < 0 || j >= list.length) return
    const next = [...list]
    ;[next[i], next[j]] = [next[j]!, next[i]!]
    save(next)
  }
  const remove = (i: number) => {
    const next = list.filter((_, at) => at !== i)
    const neighbour = next[Math.min(i, next.length - 1)]
    save(next, neighbour === undefined ? 'hs:name' : `hs:${neighbour}:remove`)
  }
  const full = list.length >= MAX_ENTRIES
  const add = (entry: string, focus?: string) => {
    const text = entry.trim()
    if (!text || full) return
    if (list.includes(text)) { toast(`${parse(text).name} is already on the list.`); return }
    save([...list, text], focus)
  }
  const typed = name.trim()

  return (
    <GlassPanel as="section" className="acct-group" testId="home-shelves">
      <SectionHeader title="Shelves on Home, for everyone" meta={list.length ? `${list.length} of ${MAX_ENTRIES}` : undefined}
        action={<GlassIconButton focusKey="hs:auto" icon="refresh" size="sm" label="Automatic" tip="Let Home choose" disabled={list.length === 0} onEnter={() => setClearing(true)} testId="home-shelves-reset" />} />
      <p className="acct-note">After New Releases, Home shows these shelves in this order. With none chosen, Home picks its own. The first {SHOWN} are shown.</p>
      {list.length === 0 ? <p className="acct-row" role="status" data-testid="home-shelves-auto"><span><strong>Automatic.</strong> Home chooses its shelves.</span></p> : (
        <FocusGroup focusKey="hs:list" className="acct-list" role="list" label="Shelves on Home, in order">
          {list.map((entry, i) => {
            const { name: shelf } = parse(entry)
            const notes = [where(entry), i >= SHOWN ? `Not shown: Home shows the first ${SHOWN}` : null].filter(Boolean).join(' · ')
            return (
              <div key={entry} className="acct-row acct-order" role="listitem" data-testid="home-shelf" data-unknown={unknown.has(entry)}>
                <span className="acct-row-main">
                  <span className="acct-row-title"><strong>{i + 1}. {shelf}</strong></span>
                  {notes && <span className="acct-row-meta">{notes}</span>}
                  {unknown.has(entry) && <span className="acct-row-meta flex items-center gap-2 flex-wrap" data-testid="home-shelf-unknown"><span className="ds-pill acct-pill" data-tone="warn">No such shelf</span>Kept, and skipped until a source has it.</span>}
                </span>
                <span className="acct-row-end">
                  <GlassIconButton focusKey={`hs:${entry}:up`} icon="up" size="sm" label={`Move ${shelf} up`} tip="Move up" disabled={i === 0} onEnter={() => move(i, -1)} />
                  <GlassIconButton focusKey={`hs:${entry}:down`} icon="down" size="sm" label={`Move ${shelf} down`} tip="Move down" disabled={i === list.length - 1} onEnter={() => move(i, 1)} />
                  <GlassIconButton focusKey={`hs:${entry}:remove`} icon="x" size="sm" label={`Remove ${shelf} from Home`} tip="Remove" onEnter={() => remove(i)} testId="home-shelf-remove" />
                </span>
              </div>
            )
          })}
        </FocusGroup>
      )}
      {full && <p className="acct-small" role="status">The list holds at most {MAX_ENTRIES} shelves. Remove one to add another.</p>}
      {!full && (['movies', 'shows'] as const).map((side) => offered(side).length > 0 && (
        <div key={side} className="acct-field">
          <h3 className="acct-kicker">Add from {SIDE_NAME[side]}</h3>
          <FocusGroup focusKey={`hs:add:${side}`} className="acct-chips" role="group" label={`Add a shelf from ${SIDE_NAME[side]}`}>
            {offered(side).map((shelf, at, all) => {
              // The chip goes once its shelf is on the list: the remote lands on its neighbour.
              const neighbour = all[at + 1] ?? all[at - 1]
              return <Chip key={shelf} focusKey={`hs:add:${side}:${shelf}`} label={`Add ${shelf} from ${SIDE_NAME[side]}`} testId="home-shelf-add"
                onEnter={() => add(entryFor(side, shelf), neighbour ? `hs:add:${side}:${neighbour}` : 'hs:auto')}>{shelf}</Chip>
            })}
          </FocusGroup>
        </div>
      ))}
      {!full && (
        <div className="acct-field">
          <h3 className="acct-kicker">Add by name</h3>
          <FocusGroup focusKey="hs:typed" className="acct-actions">
            <div style={{ width: '18rem', maxWidth: '100%' }}><FocusableInput focusKey="hs:name" label="Shelf name" placeholder="A shelf name, or movies:Name" maxLength={MAX_LENGTH} value={name} onChange={setName} onSubmit={() => { add(typed); setName('') }} /></div>
            <GlassIconButton focusKey="hs:name:add" icon="plus" label="Add" tip="Add shelf" disabled={!typed} onEnter={() => { add(typed, 'hs:name'); setName('') }} testId="home-shelf-add-name" />
          </FocusGroup>
        </div>
      )}
      <Modal open={clearing} onClose={() => setClearing(false)} title="Let Home choose its shelves?" firstFocusKey="hs:auto:keep" testId="home-shelves-reset-confirm">
        <p className="acct-note">Your list of {list.length} {list.length === 1 ? 'shelf' : 'shelves'} is cleared, and Home goes back to picking shelves by itself.</p>
        <FocusGroup focusKey="hs:auto:ask" className="acct-actions">
          <Button focusKey="hs:auto:keep" onEnter={() => setClearing(false)}>Keep my list</Button>
          <Button focusKey="hs:auto:go" danger onEnter={() => { setClearing(false); save([], 'hs:name', 'Home chooses its shelves again.') }} testId="home-shelves-reset-go">Automatic</Button>
        </FocusGroup>
      </Modal>
    </GlassPanel>
  )
}
