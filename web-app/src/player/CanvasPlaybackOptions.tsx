import { Chip } from '../components/ui'
import { useUi } from '../store/ui'

export function CanvasPlaybackOptions({ scope }: { scope: string }) {
  const enabled = useUi(s => s.canvasPlayback)
  const change = useUi(s => s.setCanvasPlayback)
  return <>
    <Chip focusKey={`${scope}:on`} label="Canvas streaming on" pressed={enabled} onEnter={() => change(true)}>On</Chip>
    <Chip focusKey={`${scope}:off`} label="Canvas streaming off" pressed={!enabled} onEnter={() => change(false)}>Off</Chip>
  </>
}
