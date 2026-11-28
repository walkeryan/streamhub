import { useEffect, useState, type RefObject } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { useGo } from '../nav/back'
import { api, ok, unwrap } from '../api/client'
import type { components } from '../api/schema'
import { Section, SectionHeader } from '../ds'
import { fmtDuration } from '../lib/util'
import { FocusableInput, FocusGroup } from '../nav/Focusable'
import { doesFocusableExist, getCurrentFocusKey, setFocus } from '../nav/spatial'
import { GlassButton } from '../player/MiniBar'
import { useSession } from '../store/session'
import { useUi } from '../store/ui'
import { Button, Modal } from './ui'

type Scene = components['schemas']['SavedScene']

function frame(video: HTMLVideoElement | null) {
  if (!video || !video.videoWidth || !video.videoHeight) return null
  try {
    const canvas = document.createElement('canvas')
    const scale = Math.min(320 / video.videoWidth, 180 / video.videoHeight, 1)
    canvas.width = Math.max(1, Math.round(video.videoWidth * scale))
    canvas.height = Math.max(1, Math.round(video.videoHeight * scale))
    const ctx = canvas.getContext('2d')
    if (!ctx) return null
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
    return canvas.toDataURL('image/jpeg', 0.65).split(',')[1] ?? null
  } catch {
    return null
  }
}

export function SaveSceneButton({
  session,
  position,
  video,
}: {
  session: string
  position: number
  video: RefObject<HTMLVideoElement | null>
}) {
  const [draft, setDraft] = useState<{ position: number; preview: string | null } | null>(null)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const profile = useSession((s) => s.profile?.id)
  const toast = useUi((s) => s.toast)
  const qc = useQueryClient()
  async function save() {
    if (!draft || !name.trim() || busy) return
    setBusy(true)
    try {
      await unwrap(
        api.POST('/viewing/bookmarks', {
          body: { session, name: name.trim(), position_ms: draft.position, preview: draft.preview },
        }),
      )
      void qc.invalidateQueries({ queryKey: ['scenes', profile] })
      toast('Scene saved.', 'success')
      setDraft(null)
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Could not save this scene.', 'error')
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <GlassButton
        focusKey="pt:scene"
        icon="bookmark"
        label="Save a scene"
        testId="save-scene"
        onEnter={() => {
          setName('')
          setDraft({ position: Math.max(0, Math.floor(position)), preview: frame(video.current) })
        }}
      />
      <Modal
        open={!!draft}
        title="Save a scene"
        firstFocusKey="scene:name"
        onClose={() => setDraft(null)}
        testId="scene-editor"
      >
        {draft?.preview && <img width="240" alt="" src={'data:image/jpeg;base64,' + draft.preview} />}
        {draft && <p>{fmtDuration(draft.position / 1000)}</p>}
        <FocusableInput
          focusKey="scene:name"
          label="Scene name"
          placeholder="Scene name"
          value={name}
          maxLength={100}
          onChange={setName}
          onSubmit={() => void save()}
        />
        <Button focusKey="scene:save" disabled={!name.trim() || busy} onEnter={() => void save()}>
          Save scene
        </Button>
        <Button focusKey="scene:cancel" onEnter={() => setDraft(null)}>
          Cancel
        </Button>
      </Modal>
    </>
  )
}

export function SavedScenes({ id }: { id: string }) {
  const profile = useSession((s) => s.profile?.id)
  const navigate = useNavigate()
  const go = useGo()
  const qc = useQueryClient()
  const toast = useUi((s) => s.toast)
  const key = ['scenes', profile, id]
  const marks = useQuery({
    queryKey: key,
    queryFn: () => unwrap<Scene[]>(api.GET('/viewing/bookmarks', { params: { query: { id } } })),
    retry: false,
  })
  async function play(scene: Scene, preview: boolean) {
    try {
      const target = await unwrap<{ id: string; variant_id: string; position_ms: number }>(
        api.GET('/viewing/bookmarks/{bookmark_id}/restore', { params: { path: { bookmark_id: scene.id } } }),
      )
      if (useSession.getState().profile?.id !== profile) return
      const params = new URLSearchParams({ variant: target.variant_id, scene_ms: String(target.position_ms) })
      if (preview) params.set('preview_scene', '1')
      const url = '/play/' + encodeURIComponent(target.id) + '?' + params
      if (preview) navigate(url)
      else go(url)
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Scene unavailable.', 'error')
    }
  }
  async function remove(scene: Scene) {
    try {
      await ok(api.DELETE('/viewing/bookmarks/{bookmark_id}', { params: { path: { bookmark_id: scene.id } } }))
      void qc.invalidateQueries({ queryKey: key })
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Could not remove this scene.', 'error')
    }
  }
  // A removed scene takes its buttons, and the focus on them, with it: the next scene gets the focus, or the
  // page's own actions when that was the last one.
  const firstScene = marks.data?.[0]?.id
  const count = marks.data?.length
  useEffect(() => {
    if (count === undefined || doesFocusableExist(getCurrentFocusKey()) || document.querySelector('.scrim, .chip-menu-scrim')) return
    void setFocus(firstScene ? 'scene:' + firstScene : 'title:watchlist')
  }, [firstScene, count])
  if (!marks.data?.length) return null
  return (
    <Section className="tp-section" label="Saved scenes" testId="saved-scenes">
      <SectionHeader title="Saved scenes" />
      {marks.data.map((mark) => (
        <FocusGroup className="flex flex-wrap items-center gap-3" key={mark.id} focusKey={'scene:' + mark.id}>
          {mark.preview && (
            <img width="128" alt="" loading="lazy" src={'data:image/jpeg;base64,' + mark.preview} />
          )}
          <strong>{mark.name}</strong>
          <span>{fmtDuration(mark.position_ms / 1000)}</span>
          <Button onEnter={() => void play(mark, true)}>Preview</Button>
          <Button onEnter={() => void play(mark, false)}>Play from scene</Button>
          <Button onEnter={() => void remove(mark)}>Remove</Button>
        </FocusGroup>
      ))}
    </Section>
  )
}
