import { useEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useParams, useSearchParams } from 'react-router'
import { createRoom, useCreateRequest, useOpenRequestFor, usePlaybackOptions, useTitle, useToggleFavorite, useToggleWatchlist, useWatchlist } from '../api/hooks'
import { api, ok } from '../api/client'
import type { PlaybackOption, Room, SourceBadge } from '../api/types'
import { Attribution, Button, Chip, Crossfade, ErrorState, Meta, Modal, Skeleton } from '../components/ui'
import { Icon } from '../components/Icon'
import { SafeImg } from '../components/SafeImg'
import { MediaTile } from '../components/Tile'
import { CatalogPicker } from '../components/CatalogPicker'
import { FocusGroup, Focusable } from '../nav/Focusable'
import { doesFocusableExist, getCurrentFocusKey, setFocus } from '../nav/spatial'
import { useRestoreFocus } from '../nav/back'
import { usePlayAction } from '../components/playAction'
import { useAdaptiveAccent } from '../lib/theme'
import { useSession } from '../store/session'
import { audioDescription } from '../player/channelAudio'
import { initialTrack, trackLabel } from '../player/audioTracks'
import { usePhone } from '../lib/layout'
import { languageName } from '../lib/util'
import { isOpenRequest, requestOf, tmdbRef } from '../lib/requests'
import { RequestStatus } from '../components/RequestStatus'
import { useUi } from '../store/ui'
import { ShareSheet } from '../room/ShareSheet'
import { useCan } from '../api/me'
import { useGo } from '../nav/back'

/** Seasons shown as chips; a series with more gets one drop-down. */
const MAX_SEASON_CHIPS = 8
/** Audio tracks shown as chips; a file with more gets one drop-down. */
const MAX_AUDIO_CHIPS = 4
const KIND_LABEL: Record<string, string> = { movie: 'Movie', show: 'Series', series: 'Series', vod: 'Movie', episode: 'Episode', season: 'Season' }

export function TitleScreen() {
  const id = decodeURIComponent(useParams().id ?? '')
  const profileId = useSession((s) => s.profile?.id)
  return <TitleDetails key={`${profileId}:${id}`} id={id} />
}

function TitleDetails({ id }: { id: string }) {
  const { data: t, error, refetch } = useTitle(id)
  const [params] = useSearchParams()
  const audio = usePlaybackOptions(t?.kind !== 'channel' && t?.sources.some((s) => s.playable_in_app) ? id : undefined)
  const [confirmation, setConfirmation] = useState<PlaybackOption | null>(null)
  // null until the viewer picks one: the server then plays the preferred language, else the file's default.
  const [pickedTrack, setPickedTrack] = useState<number | null>(null)
  const qc = useQueryClient()
  const watchlist = useWatchlist()
  const toggleFav = useToggleFavorite()
  const toggleWatch = useToggleWatchlist()
  const play = usePlayAction()
  const toast = useUi((s) => s.toast)
  const [season, setSeason] = useState<string | null>(null)
  const [picker, setPicker] = useState<null | 'play'>(params.get('choose') ? 'play' : null)
  // A phone keeps one row of primary actions; the rest wait in a menu.
  const phone = usePhone()
  const [more, setMore] = useState(false)
  useEffect(() => {
    if (!picker) return
    const frame = requestAnimationFrame(() => setFocus(confirmation ? 'audio:cancel' : 'pick:cancel'))
    return () => cancelAnimationFrame(frame)
  }, [picker, confirmation])
  const seasonQ = useTitle(season ?? undefined)
  useAdaptiveAccent(t?.backdrop ?? t?.poster)
  const createRequest = useCreateRequest()
  // null = closed; otherwise the seasons picked so far in the request dialog
  const [seasonPick, setSeasonPick] = useState<number[] | null>(null)
  // Watch together: the room made for this title, kept so opening the sheet again does not make another.
  const go = useGo()
  const [room, setRoom] = useState<{ key: string; room: Room } | null>(null)
  const [sharing, setSharing] = useState(false)
  const [startingRoom, setStartingRoom] = useState(false)
  const mayStartRoom = useCan('rooms.create')
  const requestOpen = isOpenRequest(t?.request_state)
  // The open request itself, when this profile may list it: it has the time left and the detail the title does not carry.
  const request = useOpenRequestFor(t?.id, requestOpen)
  const followed = useRef(false)
  useEffect(() => {
    // It was open and no longer is (it arrived, failed or was declined): what this page shows of the title is out of date.
    if (followed.current && !request) void qc.invalidateQueries({ queryKey: ['title'] })
    followed.current = !!request
  }, [request, qc])
  const canRequest = !!t?.requestable && !requestOpen && !!tmdbRef(t.id)
  useRestoreFocus(!!t, t?.sources.length ? 'title:play' : canRequest ? 'title:request' : 'title:watchlist')
  // The Request button leaves once a request is open (also when another profile asked first); do not strand focus on it.
  useEffect(() => {
    if (requestOpen && !doesFocusableExist(getCurrentFocusKey())) void setFocus('title:watchlist')
  }, [requestOpen])

  const { inApp, handoff } = useMemo(() => {
    const seen = new Set<string>()
    const uniq = (t?.sources ?? []).filter((s) => (seen.has(s.source_id + s.kind) ? false : (seen.add(s.source_id + s.kind), true)))
    return { inApp: uniq.filter((s) => s.playable_in_app), handoff: uniq.filter((s) => !s.playable_in_app) }
  }, [t])

  if (error) return <div className="page-pad"><ErrorState error={error} onRetry={() => void refetch()} /></div>
  if (!t) return <div className="page-pad flex gap-8"><Skeleton style={{ width: '16rem', height: '24rem' }} /><div className="flex-1 flex flex-col gap-3"><Skeleton style={{ height: '3rem', width: '60%' }} /><Skeleton style={{ height: '8rem' }} /></div></div>

  const onWatchlist = (watchlist.data ?? []).some((w) => w.id === t.id)
  const left = t.progress_pct && t.runtime_min ? Math.max(1, Math.round(t.runtime_min * (1 - t.progress_pct / 100))) : null
  const canPlay = inApp.length > 0
  const requestShown = requestOf(t)
  const playLabel = canPlay ? (left ? `Resume · ${left} min left` : 'Play') : 'Open in app'
  const episodes = season ? seasonQ.data?.episodes ?? [] : t.episodes ?? []
  const seasons = t.seasons ?? []

  // The file whose audio tracks are listed: the variant that plays by default, or the only one there is.
  const options = audio.data?.options ?? []
  const trackOption = t.kind === 'channel' ? undefined : options.find((o) => o.variant_id === audio.data?.automatic_variant_id) ?? (options.length === 1 ? options[0] : undefined)
  const tracks = trackOption?.audio_tracks ?? []
  const shownTrack = pickedTrack ?? initialTrack(tracks, trackOption?.preferred_audio_track)
  const primary = () => {
    if (canPlay) {
      if (t.kind === 'channel') return void play(t)
      // A picked track is an explicit choice of that file's audio, so it needs no second confirmation.
      if (trackOption && pickedTrack !== null) return void play(t, null, { variantId: trackOption.variant_id, audioTrack: pickedTrack })
      if (audio.data?.automatic_variant_id) return void play(t, null, { variantId: audio.data.automatic_variant_id })
      setPicker('play'); return
    }
    if (handoff.length === 1) return void play(t, handoff[0])
    setPicker('play')
  }
  const chooseVariant = (option: PlaybackOption) => {
    if (audio.data?.automatic_variant_id !== option.variant_id) { setConfirmation(option); return }
    setPicker(null); void play(t, null, { variantId: option.variant_id, audioTrack: option.variant_id === trackOption?.variant_id ? pickedTrack : null })
  }
  const choose = (s: SourceBadge) => { setPicker(null); void play(t, s) }
  const sendRequest = (seasons?: number[]) => {
    const ref = tmdbRef(t.id)
    if (!ref || createRequest.isPending) return
    setSeasonPick(null)
    createRequest.mutate({ ...ref, seasons: seasons?.length ? [...seasons].sort((a, b) => a - b) : null }, {
      onSuccess: (r) => {
        // The button is about to be replaced by the request state; move on before it goes.
        if (getCurrentFocusKey() === 'title:request') void setFocus('title:watchlist')
        toast(r.state === 'pending_approval' ? 'Requested. Waiting for approval.' : 'Requested.', 'success')
      },
      onError: (e) => toast(e.message, 'error'),
    })
  }
  const startRequest = () => { if (t.kind === 'show' && (t.request_seasons?.length ?? 0) > 1) setSeasonPick([]); else sendRequest() }
  const toggleFavorite = () => toggleFav.mutate({ kind: t.kind, id: t.id, on: !t.is_favorite })
  const openTrailer = () => window.open(`https://www.youtube.com/results?search_query=${encodeURIComponent(`${t.title} ${t.year ?? ''} trailer`)}`, '_blank', 'noopener')
  const watchTogether = async () => {
    if (startingRoom) return
    // The room plays what Play would: the picked track's file, else the automatic variant.
    const variant = t.kind === 'channel' ? null : pickedTrack !== null && trackOption ? trackOption.variant_id : audio.data?.automatic_variant_id ?? null
    const track = pickedTrack !== null && trackOption ? pickedTrack : null
    const key = `${variant}|${track}`
    if (room?.key === key) { setSharing(true); return }
    setStartingRoom(true)
    try {
      setRoom({ key, room: await createRoom({ id: t.id, variant_id: variant, audio_track: track }) })
      setSharing(true)
    } catch (e) { toast(e instanceof Error ? e.message : 'Could not start a room.', 'error') } finally { setStartingRoom(false) }
  }
  const choosable = t.kind !== 'channel' && inApp.length > 0
  // What Play will use, for the one control that stands in for the audio and version pickers.
  const playing = tracks.find((track) => track.index === shownTrack)
  const choice = [playing ? trackLabel(playing) : null, trackOption?.source_name].filter(Boolean).join(' · ') || 'Choose what to play'
  const audioChips = tracks.length > 0 && (
    <section className="flex flex-col gap-2" aria-label="Audio" data-testid="title-audio-tracks">
      <h2 className="label">Audio</h2>
      <FocusGroup focusKey="title:audio" className="flex gap-2 flex-wrap">
        {/* A file dubbed into many languages gets one control, like a long list of seasons. */}
        {tracks.length > MAX_AUDIO_CHIPS
          ? <CatalogPicker scope="title:audio-pick" label="Audio" search={false} testId="audio-picker" value={String(shownTrack ?? '')}
              options={tracks.map((track) => ({ id: String(track.index), label: trackLabel(track) }))} onChange={(picked) => setPickedTrack(Number(picked))} />
          : tracks.map((track) => (
            <Chip key={track.index} focusKey={`title:audio:${track.index}`} pressed={track.index === shownTrack} onEnter={() => setPickedTrack(track.index)}>{trackLabel(track)}</Chip>
          ))}
      </FocusGroup>
    </section>
  )
  const markWatched = async () => {
    const dur = (t.runtime_min ?? 0) * 60_000
    try {
      await ok(api.POST('/playback/progress', { body: { id: t.id, position_ms: dur, duration_ms: dur || null, state: 'stopped' } }))
      for (const key of ['home', 'search', 'browse', 'title']) void qc.invalidateQueries({ queryKey: [key] })
      toast('Marked as watched.', 'success')
    } catch { toast('Could not save watched status. Try again.', 'error') }
  }

  return (
    <div data-testid="title-detail" style={{ position: 'relative' }}>
      <div style={{ position: 'absolute', inset: '0 0 auto 0', height: 'min(70dvh, 44rem)', overflow: 'hidden' }} aria-hidden="true">
        <Crossfade src={t.backdrop ?? t.poster} />
        <div style={{ position: 'absolute', inset: 0, zIndex: 1, background: 'linear-gradient(to top, var(--t-bg) 6%, transparent 70%), linear-gradient(to right, var(--t-bg) 4%, transparent 70%)' }} />
      </div>
      <div className="page-pad title-page" style={{ position: 'relative', zIndex: 2 }}>
        <div className="title-top">
          <div className="tile tile-poster title-poster">
            <SafeImg className="art" src={t.poster} alt="" fallback={<div className="grid place-items-center h-full p-4 text-center heading">{t.title}</div>} />
          </div>
          <div className="title-head flex flex-col gap-4 min-w-0">
            <h1 className="display">{t.title}</h1>
            <Meta items={[KIND_LABEL[t.kind], t.year, t.runtime_min ? `${t.runtime_min} min` : null, t.genres?.join(', '), t.rating ? `★ ${t.rating.toFixed(1)}` : null]} />
          </div>
          <div className="title-body flex flex-col gap-4 min-w-0">
            {t.overview && <p style={{ lineHeight: 1.55 }}>{t.overview}</p>}
            {requestShown && (
              <RequestStatus testId="title-request-state" progress={request?.progress ?? requestShown}>
                {requestShown.phase === 'available' ? 'Not in your library yet: it appears after the next library sync.' : null}
              </RequestStatus>
            )}
            <FocusGroup focusKey="title:actions" className={phone ? 'title-actions' : 'flex gap-3 flex-wrap items-center'} preferred="title:play" data={{ together: canPlay && mayStartRoom }}>
              {(canPlay || handoff.length > 0) && <Button focusKey="title:play" primary icon="play" onEnter={primary} testId="title-play">{canPlay && t.kind !== 'channel' && !audio.data?.automatic_variant_id && pickedTrack === null ? 'Choose audio / source' : playLabel}</Button>}
              {canPlay && mayStartRoom && <Button focusKey="title:together" icon="people" iconOnly={phone} label={phone ? 'Watch together' : undefined} disabled={startingRoom} onEnter={() => void watchTogether()} testId="title-together">{phone ? null : startingRoom ? 'Starting a room…' : 'Watch together'}</Button>}
              {!phone && choosable && <Button focusKey="title:source" icon="source" onEnter={() => setPicker('play')} testId="title-source">Choose source</Button>}
              {canRequest && <Button focusKey="title:request" primary={!handoff.length} icon="plus" onEnter={startRequest} testId="title-request">{t.request_state === 'declined' || t.request_state === 'failed' ? 'Request again' : 'Request'}</Button>}
              <Button focusKey="title:watchlist" icon={onWatchlist ? 'check' : 'bookmark'} pressed={onWatchlist} onEnter={() => toggleWatch.mutate({ id: t.id, on: !onWatchlist })}>
                {phone ? 'My List' : onWatchlist ? 'On watchlist' : 'Watchlist'}
              </Button>
              {phone ? <Button focusKey="title:more" icon="menu" iconOnly label="More actions" testId="title-more" onEnter={() => setMore(true)} /> : <>
                <Button focusKey="title:fav" icon="heart" pressed={!!t.is_favorite} onEnter={toggleFavorite}>
                  {t.is_favorite ? 'Favorited' : 'Favorite'}
                </Button>
                <Button focusKey="title:trailer" icon="external" onEnter={openTrailer}>Trailer</Button>
                {canPlay && <Button focusKey="title:watched" icon="eye" onEnter={markWatched}>Mark watched</Button>}
              </>}
            </FocusGroup>
            {phone ? choosable && (
              <Button focusKey="title:source" className="title-choice" label={`Audio and version: ${choice}`} onEnter={() => setPicker('play')} testId="title-source">
                <span className="flex flex-col min-w-0"><span className="label">Audio &amp; version</span><span className="truncate">{choice}</span></span>
                <Icon name="chevR" size={20} />
              </Button>
            ) : audioChips}
            {t.cast && t.cast.length > 0 && <p className="label">Cast: {t.cast.map((c) => c.name).join(', ')}</p>}
          </div>
        </div>

        {seasons.length > 0 && (
          <section className="mt-8" aria-label="Seasons and episodes" data-testid="episodes">
            <FocusGroup focusKey="title:seasons" className="flex gap-2 flex-wrap mb-2">
              {/* A long-running series would fill the screen with chips: past a row's worth, one control opens the list. */}
              {seasons.length > MAX_SEASON_CHIPS
                ? <CatalogPicker scope="title:season" label="Season" search={false} testId="season-picker" value={season ?? seasons[0]?.id ?? ''}
                    options={seasons.map((s) => ({ id: s.id, label: s.title }))} onChange={(picked) => setSeason(picked === seasons[0]?.id ? null : picked)} />
                : seasons.map((s, i) => (
                  <Chip key={s.id} focusKey={`season:${s.id}`} pressed={(season ?? seasons[0]?.id) === s.id} onEnter={() => setSeason(i === 0 ? null : s.id)}>{s.title}</Chip>
                ))}
            </FocusGroup>
            <FocusGroup focusKey="title:episodes" className="row-scroll" style={{ paddingInline: 0 }}>
              {episodes.map((e) => (
                <MediaTile key={e.id} tile={{ ...e, title: e.title }} scope="episodes" layout="landscape" titleAlways onOpen={() => void play(e)} />
              ))}
            </FocusGroup>
          </section>
        )}

        <section className="mt-6 flex flex-col gap-2" aria-label="Available on" data-testid="available-on">
          <h2 className="heading">Available on</h2>
          <FocusGroup focusKey="title:avail" className="flex gap-2 flex-wrap">
            {[...inApp, ...handoff].map((s) => (
              <Focusable key={s.source_id + s.kind} focusKey={`avail:${s.source_id}:${s.kind}`} className="chip fx-flat" onEnter={() => s.playable_in_app && t.kind !== 'channel' ? setPicker('play') : void play(t, s)}
                label={`${s.playable_in_app ? (t.kind === 'channel' ? 'Play' : 'Choose audio') : 'Open in app'}: ${s.name}`}>
                <span className={s.playable_in_app ? 'badge badge-play' : 'badge'}>{s.playable_in_app ? (t.kind === 'channel' ? 'Play' : 'Choose audio') : 'Open in app'}</span>
                {s.name}{s.quality ? ` ${s.quality}` : ''}{s.kind && !s.playable_in_app ? ` · ${s.kind}` : ''}
              </Focusable>
            ))}
            {!inApp.length && !handoff.length && <span className="muted">Not available from your sources.</span>}
          </FocusGroup>
          <Attribution text={t.attribution ?? 'Availability data by JustWatch'} />
        </section>
      </div>

      <Modal open={picker !== null} onClose={() => { setPicker(null); setConfirmation(null) }} title={confirmation ? 'Confirm audio choice' : canPlay ? (phone ? 'Audio & version' : 'Choose audio and source') : 'Open in'} firstFocusKey="pick:cancel" variant="sheet">
        <div className="flex flex-col gap-2">
          {confirmation ? <>
            <p>{confirmation.audio_verified ? audioDescription(confirmation) : 'Audio language is unverified.'} This source is not automatically selected for your language preferences.</p>
            <p className="label">{confirmation.provider_label}</p>
            <Button focusKey="audio:cancel" onEnter={() => setConfirmation(null)}>Back to sources</Button>
            <Button focusKey="audio:confirm" onEnter={() => { const selected = confirmation; setConfirmation(null); setPicker(null); void play(t, null, { variantId: selected.variant_id, allowUnverifiedAudio: true, audioTrack: selected.variant_id === trackOption?.variant_id ? pickedTrack : null }) }}>Play this source anyway</Button>
          </> : canPlay ? <>
            {phone && audioChips}
            {phone && tracks.length > 0 && <h2 className="label">Version</h2>}
            {audio.isPending && <p role="status">Checking available sources…</p>}
            {audio.error && <ErrorState error={audio.error} onRetry={() => void audio.refetch()} />}
            {/* Two short lines per source, each cut with an ellipsis: a long label must never widen the sheet. */}
            {audio.data?.options.map((option, i) => <Button key={option.variant_id} focusKey={`pick:${i}`} className="source-choice" onEnter={() => chooseVariant(option)}
              label={`${option.source_name} · ${audioDescription(option)}${option.provider_label ? ` · ${option.provider_label}` : ''}`}>
              <span className="truncate"><strong>{option.source_name}</strong>{option.provider_label && option.provider_label !== option.source_name ? ` · ${option.provider_label}` : ''}</span>
              <span className="label truncate">{audioDescription(option)}{!option.audio_verified && option.language_hint ? ` · Listed as ${languageName(option.language_hint)}` : ''}</span>
            </Button>)}
            {audio.data && !audio.data.options.length && <p>No playable sources are available.</p>}
          </> : handoff.map((s, i) => <Button key={s.source_id + s.kind} focusKey={`pick:${i}`} onEnter={() => choose(s)}>{s.name}</Button>)}
          <Button focusKey="pick:cancel" ghost onEnter={() => { setPicker(null); setConfirmation(null) }}>Cancel</Button>
        </div>
      </Modal>

      <ShareSheet room={sharing ? room?.room ?? null : null} onClose={() => setSharing(false)} onStart={() => room && go(`/room/${room.room.code}?start=1`)} />

      <Modal open={more} onClose={() => setMore(false)} title={t.title} firstFocusKey="title:fav" variant="sheet" testId="title-more-menu">
        <FocusGroup focusKey="title:more-list" className="flex flex-col gap-2">
          <Focusable focusKey="title:fav" className="menu-item fx-flat" aria-pressed={!!t.is_favorite} onEnter={toggleFavorite}><Icon name="heart" filled={!!t.is_favorite} />{t.is_favorite ? 'Remove from favorites' : 'Add to favorites'}</Focusable>
          <Focusable focusKey="title:trailer" className="menu-item fx-flat" onEnter={() => { setMore(false); openTrailer() }}><Icon name="external" />Trailer</Focusable>
          {canPlay && <Focusable focusKey="title:watched" className="menu-item fx-flat" onEnter={() => { setMore(false); void markWatched() }}><Icon name="eye" />Mark watched</Focusable>}
          <Button focusKey="title:more-close" ghost onEnter={() => setMore(false)}>Close</Button>
        </FocusGroup>
      </Modal>

      <Modal open={seasonPick !== null} onClose={() => setSeasonPick(null)} title={`Request ${t.title}`} firstFocusKey="reqpick:all">
        <div className="flex flex-col gap-3" data-testid="request-seasons">
          <Button focusKey="reqpick:all" primary onEnter={() => sendRequest()}>All seasons</Button>
          <p className="label">Or pick seasons</p>
          <FocusGroup focusKey="reqpick:seasons" className="flex gap-2 flex-wrap">
            {(t.request_seasons ?? []).map((n) => (
              <Chip key={n} focusKey={`reqpick:s:${n}`} pressed={!!seasonPick?.includes(n)}
                onEnter={() => setSeasonPick((cur) => (cur === null ? cur : cur.includes(n) ? cur.filter((x) => x !== n) : [...cur, n]))}>Season {n}</Chip>
            ))}
          </FocusGroup>
          <Button focusKey="reqpick:some" disabled={!seasonPick?.length} onEnter={() => sendRequest(seasonPick ?? [])}>Request selected seasons</Button>
          <Button focusKey="reqpick:cancel" ghost onEnter={() => setSeasonPick(null)}>Cancel</Button>
        </div>
      </Modal>
    </div>
  )
}
