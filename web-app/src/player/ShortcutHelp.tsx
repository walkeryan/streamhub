import { Button, Modal } from '../components/ui'
import { PadGlyphs } from '../components/PadHints'
import { FocusGroup } from '../nav/Focusable'
import { inShell } from '../shell'
import { CONTROLLER_HELP, SHORTCUT_HELP, type ShortcutLine } from './shortcuts'

/** The sheet's title and the menu entry that opens it. The TV app reads no controller itself (nav/gamepad.ts). */
export const helpTitle = (): string => (inShell() ? 'Keyboard shortcuts' : 'Keyboard and controller')

/**
 * The keyboard shortcuts, as the key handler has them (shortcuts.ts), and a game controller's buttons.
 * A live channel and a film differ.
 */
export function ShortcutHelp({ open, live, onClose }: { open: boolean; live: boolean; onClose: () => void }) {
  const applies = (line: Pick<ShortcutLine, 'only'>) => !line.only || (line.only === 'live') === live
  return (
    <Modal open={open} onClose={onClose} title={helpTitle()} firstFocusKey="keys:close" wide testId="player-shortcuts">
      <div className="keys">
        {SHORTCUT_HELP.map((group) => (
          <section key={group.title}>
            <h3 className="label">{group.title}</h3>
            {group.lines.filter(applies).map((line) => (
              <div key={line.does} className="keys-line">
                <span>{line.keys.map((key) => (key === '…' ? <span key={key} aria-hidden="true">…</span> : <kbd key={key} className="kbd">{key}</kbd>))}</span>
                <span>{line.does}</span>
              </div>
            ))}
          </section>
        ))}
        {!inShell() && (
          <section className="keys-pad" data-testid="controller-help">
            <h3 className="label">Controller</h3>
            {CONTROLLER_HELP.filter(applies).map((line) => (
              <div key={line.does} className="keys-line">
                <PadGlyphs buttons={line.buttons} />
                <span>{line.does}</span>
              </div>
            ))}
          </section>
        )}
      </div>
      <p className="muted label">A remote needs none of these: the arrows, OK and Back reach every control.</p>
      <FocusGroup focusKey="keys" className="flex gap-3">
        <Button focusKey="keys:close" primary onEnter={onClose}>Close</Button>
      </FocusGroup>
    </Modal>
  )
}
