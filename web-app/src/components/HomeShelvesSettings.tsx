import { useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, ok } from '../api/client'
import { qk, useBrowseShelves, useSettings } from '../api/hooks'
import { FocusGroup, FocusableInput } from '../nav/Focusable'
import { setFocus } from '../nav/spatial'
import { useUi } from '../store/ui'
import { Button, Chip, ErrorState, Modal } from './ui'

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
  if (!settings.data) return <p className="muted">Loading…</p>

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
    <section className="flex flex-col gap-3" data-testid="home-shelves">
      <h2 className="heading">Shelves on Home, for everyone</h2>
      <p className="muted">After New Releases, Home shows these shelves in this order. With none chosen, Home picks its own. The first {SHOWN} are shown.</p>
      {list.length === 0 ? <p className="panel" role="status" data-testid="home-shelves-auto"><strong>Automatic.</strong> Home chooses its shelves.</p> : (
        <FocusGroup focusKey="hs:list" className="flex flex-col gap-2" role="list" label="Shelves on Home, in order">
          {list.map((entry, i) => {
            const { name: shelf } = parse(entry)
            const notes = [where(entry), i >= SHOWN ? `Not shown: Home shows the first ${SHOWN}` : null].filter(Boolean).join(' · ')
            return (
              <div key={entry} className="panel flex items-center gap-2" role="listitem" data-testid="home-shelf" data-unknown={unknown.has(entry)}>
                <span className="flex flex-col flex-1 min-w-0">
                  <span className="truncate" style={{ fontWeight: 600 }}>{i + 1}. {shelf}</span>
                  {notes && <span className="label">{notes}</span>}
                  {unknown.has(entry) && <span className="label flex items-center gap-2 flex-wrap" data-testid="home-shelf-unknown"><span className="badge badge-warn">No such shelf</span>Kept, and skipped until a source has it.</span>}
                </span>
                <Button focusKey={`hs:${entry}:up`} iconOnly icon="up" label={`Move ${shelf} up`} disabled={i === 0} onEnter={() => move(i, -1)} />
                <Button focusKey={`hs:${entry}:down`} iconOnly icon="down" label={`Move ${shelf} down`} disabled={i === list.length - 1} onEnter={() => move(i, 1)} />
                <Button focusKey={`hs:${entry}:remove`} iconOnly icon="x" label={`Remove ${shelf} from Home`} onEnter={() => remove(i)} testId="home-shelf-remove" />
              </div>
            )
          })}
        </FocusGroup>
      )}
      {full && <p className="label" role="status">The list holds at most {MAX_ENTRIES} shelves. Remove one to add another.</p>}
      {!full && (['movies', 'shows'] as const).map((side) => offered(side).length > 0 && (
        <div key={side} className="flex flex-col gap-2">
          <h3 className="label">Add from {SIDE_NAME[side]}</h3>
          <FocusGroup focusKey={`hs:add:${side}`} className="flex gap-2 flex-wrap" role="group" label={`Add a shelf from ${SIDE_NAME[side]}`}>
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
        <div className="flex flex-col gap-2">
          <h3 className="label">Add by name</h3>
          <FocusGroup focusKey="hs:typed" className="flex gap-3 flex-wrap items-center">
            <div style={{ width: '18rem', maxWidth: '100%' }}><FocusableInput focusKey="hs:name" label="Shelf name" placeholder="A shelf name, or movies:Name" maxLength={MAX_LENGTH} value={name} onChange={setName} onSubmit={() => { add(typed); setName('') }} /></div>
            <Button focusKey="hs:name:add" icon="plus" disabled={!typed} onEnter={() => { add(typed, 'hs:name'); setName('') }} testId="home-shelf-add-name">Add</Button>
          </FocusGroup>
        </div>
      )}
      <div><Button focusKey="hs:auto" icon="refresh" disabled={list.length === 0} onEnter={() => setClearing(true)} testId="home-shelves-reset">Automatic</Button></div>
      <Modal open={clearing} onClose={() => setClearing(false)} title="Let Home choose its shelves?" firstFocusKey="hs:auto:keep" testId="home-shelves-reset-confirm">
        <p className="muted">Your list of {list.length} {list.length === 1 ? 'shelf' : 'shelves'} is cleared, and Home goes back to picking shelves by itself.</p>
        <FocusGroup focusKey="hs:auto:ask" className="flex gap-3 flex-wrap">
          <Button focusKey="hs:auto:keep" onEnter={() => setClearing(false)}>Keep my list</Button>
          <Button focusKey="hs:auto:go" danger onEnter={() => { setClearing(false); save([], 'hs:name', 'Home chooses its shelves again.') }} testId="home-shelves-reset-go">Automatic</Button>
        </FocusGroup>
      </Modal>
    </section>
  )
}
