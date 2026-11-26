import { useEffect, useRef, useState } from 'react'
import { useHome } from '../api/hooks'
import { artSizes, captionBackgrounds, captionColors, captionSizes, useArtSize, useCaptions } from '../api/viewingPreferences'
import { GlassPanel, SectionHeader } from '../ds'
import { Focusable, FocusGroup } from '../nav/Focusable'
import { Button } from './ui'

function Choices<T extends string>({ label, items, value, change, disabled }: {
  label: string; items: readonly T[]; value: T; change: (item: T) => void; disabled: boolean
}) {
  return <div className="appearance-field">
    <span>{label}</span>
    <FocusGroup focusKey={'appearance:' + label} className="appearance-options" role="radiogroup" label={label}>
      {items.map((item) => <Focusable key={item} focusKey={label + ':' + item} role="radio"
        aria={{ checked: item === value }} disabled={disabled} onEnter={() => change(item)}
        className="chip fx-flat">{item.charAt(0).toUpperCase() + item.slice(1)}</Focusable>)}
    </FocusGroup>
  </div>
}

export function ViewingAppearance() {
  const art = useArtSize()
  const captions = useCaptions()
  const [draft, setDraft] = useState<typeof captions.value | null>(null)
  const preview = draft ?? captions.value
  const saveCaptions = () => {
    if (!draft) return
    captions.update(draft)
    setDraft(null)
  }
  const home = useHome()
  const poster = home.data?.rows?.flatMap((row) => row.tiles).find((tile) => tile.poster)
  const video = useRef<HTMLVideoElement>(null)
  useEffect(() => {
    const track = video.current?.textTracks[0]
    if (track) track.mode = 'showing'
  }, [])
  return <>
    <GlassPanel as="section" className="acct-group">
      <SectionHeader title="Box art" />
      <Choices label="Box art size" items={artSizes} value={art.value} disabled={!art.ready} change={art.update} />
      <div className="appearance-poster-preview" data-size={art.value} data-testid="poster-preview" aria-hidden="true">
        {poster?.poster && <img src={poster.poster} alt="" />}
      </div>
      {art.error && <p role="alert">{art.error} <Button onEnter={() => void art.retry()}>Retry</Button></p>}
    </GlassPanel>
    <GlassPanel as="section" className="acct-group">
      <SectionHeader title="Captions" />
      <div className="caption-preview" data-caption-size={preview.size} data-caption-color={preview.color}
        data-caption-background={preview.background}>
        <video ref={video} src="/appearance/sample.mp4" loop autoPlay muted playsInline controls aria-label="Caption preview">
          <track kind="captions" src="/appearance/sample.vtt" srcLang="en" label="Preview captions" default />
        </video>
      </div>
      <Choices label="Caption size" items={captionSizes} value={preview.size} disabled={!captions.ready}
        change={(size) => setDraft({ ...preview, size })} />
      <Choices label="Caption color" items={captionColors} value={preview.color} disabled={!captions.ready}
        change={(color) => setDraft({ ...preview, color })} />
      <Choices label="Caption background" items={captionBackgrounds} value={preview.background} disabled={!captions.ready}
        change={(background) => setDraft({ ...preview, background })} />
      <Button focusKey="captions:save" disabled={!draft || !captions.ready || captions.saving} onEnter={saveCaptions}>
        {captions.saving ? 'Saving…' : 'Save caption appearance'}
      </Button>
      {captions.error && <p role="alert">{captions.error} <Button onEnter={() => void captions.retry()}>Retry</Button></p>}
    </GlassPanel>
  </>
}
