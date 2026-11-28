import { useRef, useState } from 'react'
import { Button, Chip, Modal } from '../components/ui'
import { PadGlyphs } from '../components/PadHints'
import { FocusGroup, Focusable, type FocusableHandle } from '../nav/Focusable'
import { inShell } from '../shell'
import { CONTROLLER_HELP, SHORTCUT_HELP, type ShortcutLine } from './shortcuts'
import '../styles/shortcuts.css'

export const helpTitle = () => 'Controls and shortcuts'
const modes = ['Keyboard', 'Remote', 'Touch', 'Controller'] as const
type Mode = typeof modes[number]

export function ShortcutHelp({ open, live, onClose }: { open: boolean; live?: boolean; onClose: () => void }) {
  const initial: Mode = inShell() ? 'Remote' : window.matchMedia('(pointer: coarse)').matches ? 'Touch' : 'Keyboard'
  const available = modes.filter(m => m !== 'Controller' || !inShell())
  const [mode, setMode] = useState<Mode>(initial)
  const guide = useRef<FocusableHandle>(null)
  const applies = (line: Pick<ShortcutLine, 'only'>) => live === undefined || !line.only || (line.only === 'live') === live
  const context = (line: Pick<ShortcutLine, 'only'>) => live === undefined && line.only ? (line.only === 'film' ? ' · On demand' : ' · Live TV') : ''
  const select = (next: Mode) => {
    setMode(next)
    guide.current?.el?.scrollTo(0, 0)
  }
  const scroll = (direction: string) => {
    const el = guide.current?.el
    if (!el || (direction !== 'up' && direction !== 'down')) return true
    const top = el.scrollTop
    el.scrollTop += (direction === 'up' ? -1 : 1) * Math.max(32, el.clientHeight * .75)
    return Math.abs(top - el.scrollTop) < 1
  }
  return (
    <Modal open={open} onClose={onClose} title={helpTitle()} firstFocusKey="keys:guide" wide testId="player-shortcuts">
      <FocusGroup focusKey="keys:modes" className="lib-chips" role="tablist" label="Input method">
        {available.map(m => <Chip key={m} focusKey={`keys:tab:${m}`} tab pressed={mode === m} onEnter={() => select(m)}>{m}</Chip>)}
      </FocusGroup>
      {available.filter(m => m !== mode).map(m => <div key={m} id={`keys:tab:${m}:panel`} role="tabpanel" aria-labelledby={`keys:tab:${m}`} hidden />)}
      <Focusable ref={guide} id={`keys:tab:${mode}:panel`} focusKey="keys:guide" role="tabpanel" aria={{ labelledby: `keys:tab:${mode}` }} className="shortcut-guide" onArrow={scroll}>
        {mode === 'Keyboard' && <div className="keys">
          <section><h3 className="label">Browsing</h3>
            <div className="keys-line"><span><kbd className="kbd">Tab</kbd><kbd className="kbd">Shift Tab</kbd></span><span>Next or previous control</span></div>
            <div className="keys-line"><span><kbd className="kbd">/</kbd></span><span>Type a search</span></div>
            <div className="keys-line"><span><kbd className="kbd">Hold Enter</kbd></span><span>More actions for a poster or a channel</span></div>
            <div className="keys-line"><span><kbd className="kbd">Esc</kbd></span><span>Close a menu or go back</span></div>
          </section>
          {SHORTCUT_HELP.map(group => <section key={group.title}>
            <h3 className="label">{group.title}</h3>
            {group.lines.filter(applies).map(line => <div key={line.does} className="keys-line">
              <span>{line.keys.map(key => <kbd key={key} className="kbd">{key}</kbd>)}</span><span>{line.does}{context(line)}</span>
            </div>)}
          </section>)}
        </div>}
        {mode === 'Remote' && <div className="shortcut-instructions">
          <p><strong>Arrows</strong> move between controls. Press <strong>OK</strong> to select.</p>
          <p><strong>Hold OK</strong> on a poster or a channel for more: My List, thumbs up or down.</p>
          {live !== true && <p>In a movie or episode, select the timeline and use <strong>Left</strong> or <strong>Right</strong> to seek.</p>}
          <p>Select <strong>Search</strong> to speak. Select it again to type. If voice is unavailable, the keyboard opens immediately.</p>
          <p><strong>Back</strong> closes the open menu or returns to the previous screen.</p>
        </div>}
        {mode === 'Touch' && <div className="shortcut-instructions">
          <p><strong>Press and hold</strong> a poster or a channel for more: My List, thumbs up or down.</p>
          <p><strong>Tap the picture</strong> to show or hide playback controls.</p>
          {live !== true && <p>On seekable video, <strong>double-tap the left half</strong> to go back 10 seconds or <strong>the right half</strong> to go forward 10 seconds.</p>}
          <p>Use the player's <strong>Back</strong> control to return to browsing.</p>
        </div>}
        {mode === 'Controller' && <div className="keys"><section className="keys-pad" data-testid="controller-help">
          <h3 className="label">Controller</h3>
          {CONTROLLER_HELP.filter(applies).map(line => <div key={line.does} className="keys-line">
            <PadGlyphs buttons={line.buttons} /><span>{line.does}{context(line)}</span>
          </div>)}
        </section></div>}
      </Focusable>
      <Button focusKey="keys:close" primary onEnter={onClose}>Close</Button>
    </Modal>
  )
}
