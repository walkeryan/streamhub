import { useEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router'
import {
  createRoom, useCreateRequest, useOpenRequestFor, usePlaybackOptions, useSimilarTitles, useTitle, useTitleActivity, useToggleFavorite, useToggleWatchlist, useWatchlist,
} from '../api/hooks'
import { api, ok } from '../api/client'
import type { EpisodeTile, PlaybackOption, Room, SourceBadge } from '../api/types'
import { Attribution, Button, Chip, Crossfade, ErrorState, Modal, ProgressBar, Skeleton } from '../components/ui'
import { SafeImg } from '../components/SafeImg'
import { CatalogPicker } from '../components/CatalogPicker'
import { Popover } from '../components/ChipMenu'
import { FocusGroup, Focusable, type FocusableHandle } from '../nav/Focusable'
import { doesFocusableExist, getCurrentFocusKey, setFocus } from '../nav/spatial'
import { useBackHandler, useGo, useRestoreFocus } from '../nav/back'
import { useHistoryState } from '../nav/historyState'
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
import { handOff, inShell } from '../shell'
import { TitleIcon } from './title/icons'
import { ActivityRow, CastRow, EpisodeCard, SimilarRow } from './title/Sections'

/** Seasons shown as chips; a series with more gets one drop-down. */
const MAX_SEASON_CHIPS = 8
/** Audio tracks shown as chips; a file with more gets one drop-down. */
const MAX_AUDIO_CHIPS = 4
const titlePath = (id: string) => `/title/${encodeURIComponent(id)}`
type IconName = Parameters<typeof TitleIcon>[0]['name']

export function TitleScreen() {
  const id = decodeURIComponent(useParams().id ?? '')
  const profileId = useSession((s) => s.profile?.id)
  return <TitleDetails key={`${profileId}:${id}`} id={id} />
}

function formatDate(iso: string | null | undefined): string | null {
  if (!iso || !/^\d{4}-\d{2}-\d{2}/.test(iso)) return null
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  return new Date(Date.UTC(y!, m! - 1, d!)).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' })
}

/** A round glass button with no words: its label is for assistive tech, and shows under it on focus or hover. */
function Glass({ focusKey, icon, label, tip, pressed, onEnter, testId, buttonRef, expanded }: {
  focusKey: string; icon: IconName; label: string; tip?: string; pressed?: boolean; onEnter: () => void; testId?: string
  buttonRef?: React.Ref<FocusableHandle>; expanded?: boolean
}) {
  return (
    <span className="tp-glass-wrap">
      <Focusable ref={buttonRef} focusKey={focusKey} className="tp-glass" label={label} aria-pressed={pressed} onEnter={onEnter} data-testid={testId}
        aria={expanded === undefined ? undefined : { haspopup: 'menu', expanded }}>
        <TitleIcon name={icon} filled={icon === 'list' && pressed ? true : undefined} />
      </Focusable>
      <span className="tp-tip" aria-hidden="true">{tip ?? label}</span>
    </span>
  )
}

type MenuItem = { key: string; icon: IconName; label: string; onEnter: () => void; testId?: string; pressed?: boolean; disabled?: boolean; group: 1 | 2 }

function TitleDetails({ id }: { id: string }) {
  const { data: t, error, refetch } = useTitle(id)
  const [params] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const audio = usePlaybackOptions(t?.kind !== 'channel' && t?.sources.some((s) => s.playable_in_app) ? id : undefined)
  // null until the viewer picks one: the server then plays the preferred language, else the file's default.
  const [pickedTrack, setPickedTrack] = useState<number | null>(null)
  const qc = useQueryClient()
  const watchlist = useWatchlist()
  const toggleFav = useToggleFavorite()
  const toggleWatch = useToggleWatchlist()
  const play = usePlayAction()
  const toast = useUi((s) => s.toast)
  const go = useGo()
  const phone = usePhone()
  const [picker, setPicker] = useState<null | 'play'>(params.get('choose') ? 'play' : null)
  const [more, setMore] = useState(false)
  const moreButton = useRef<FocusableHandle>(null)
  useEffect(() => {
    if (!picker) return
    const frame = requestAnimationFrame(() => setFocus('pick:cancel'))
    return () => cancelAnimationFrame(frame)
  }, [picker])

  // Where this title sits: an episode and a season name their show (and season) and link to them.
  const parent = t?.parent ?? null
  const nested = t?.kind === 'episode' || t?.kind === 'season'
  const showId = nested ? parent?.show?.id : undefined
  // The show's page has the seasons to switch between and the cast; an episode's or a season's page borrows them.
  const showQ = useTitle(showId)
  const ownSeasons = t?.seasons ?? []
  const pageSeason = parent?.season
  const seasons: EpisodeTile[] = ownSeasons.length ? ownSeasons
    : showQ.data?.seasons?.length ? showQ.data.seasons
      : pageSeason ? [{ id: pageSeason.id, kind: 'season', title: pageSeason.title, season_number: pageSeason.number, sources: [], is_favorite: false }] : []
  // The season the strip opens on: a series' first; for a season or an episode, its own (matched by number
  // when the show's list comes from another library copy).
  const defaultSeason = !nested ? seasons[0]?.id
    : pageSeason ? (seasons.find((s) => s.id === pageSeason.id) ?? seasons.find((s) => pageSeason.number != null && s.season_number === pageSeason.number))?.id ?? pageSeason.id : undefined
  const [pickedSeason, setPickedSeason] = useHistoryState<string | null>('season', null)
  const stripSeason = pickedSeason ?? defaultSeason ?? null
  // The episodes already in this page's payload (a series' first season, a season's own), else the season's page.
  const inline = (t?.kind === 'season' && stripSeason === defaultSeason) || (!nested && (!seasons.length || stripSeason === seasons[0]?.id))
  const fetchSeason = inline || !t ? undefined : t.kind === 'episode' && stripSeason === defaultSeason ? pageSeason?.id : stripSeason ?? undefined
  const seasonQ = useTitle(fetchSeason)
  const episodes: EpisodeTile[] = (inline ? t?.episodes : seasonQ.data?.episodes) ?? []

  // The household's activity and More like this are about the series, so its episodes share one answer.
  const extrasId = t && t.kind !== 'channel' ? (showId ?? t.id) : undefined
  const activity = useTitleActivity(extrasId)
  const similar = useSimilarTitles(extrasId)
  useAdaptiveAccent(parent?.show?.backdrop ?? t?.backdrop ?? t?.poster)

  const createRequest = useCreateRequest()
  // null = closed; otherwise the seasons picked so far in the request dialog
  const [seasonPick, setSeasonPick] = useState<number[] | null>(null)
  // Watch together: the room made for this title, kept so opening the sheet again does not make another.
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
  const { inApp, handoff } = useMemo(() => {
    const seen = new Set<string>()
    const uniq = (t?.sources ?? []).filter((s) => (seen.has(s.source_id + s.kind) ? false : (seen.add(s.source_id + s.kind), true)))
    return { inApp: uniq.filter((s) => s.playable_in_app), handoff: uniq.filter((s) => !s.playable_in_app) }
  }, [t])
  useRestoreFocus(!!t, inApp.length || handoff.length ? 'title:play' : canRequest ? 'title:request' : 'title:watchlist')
  // The Request button leaves once a request is open (also when another profile asked first); do not strand focus on it.
  useEffect(() => {
    if (requestOpen && !doesFocusableExist(getCurrentFocusKey())) void setFocus('title:watchlist')
  }, [requestOpen])

  // Back climbs: from an episode to its season, from a season to its show. Arriving from another title page
  // (the show's strip, More like this) Back simply returns there. Arriving from anywhere else (Home, search,
  // a link, the first page of the TV app) the episode's entry is replaced by its season's, so the next Back
  // reaches the show, and the one after that where the viewer came from.
  const from = (location.state as { from?: unknown } | null)?.from
  const up = t?.kind === 'episode' ? (parent?.season?.id ?? parent?.show?.id) : t?.kind === 'season' ? parent?.show?.id : undefined
  useBackHandler(() => {
    if (!up || (typeof from === 'string' && from.startsWith('/title/'))) return false
    navigate(titlePath(up), { replace: true, state: { from } })
    return true
  }, !!up)

  if (error) return <div className="page-pad"><ErrorState error={error} onRetry={() => void refetch()} /></div>
  if (!t) return <div className="page-pad flex gap-8"><Skeleton style={{ width: '23.5rem', aspectRatio: '16 / 9' }} /><div className="flex-1 flex flex-col gap-3"><Skeleton style={{ height: '3rem', width: '60%' }} /><Skeleton style={{ height: '8rem' }} /></div></div>

  const onWatchlist = (watchlist.data ?? []).some((w) => w.id === t.id)
  const pct = t.progress_pct ?? 0
  const watched = pct >= 100
  const left = pct > 0 && pct < 100 && t.runtime_min ? Math.max(1, Math.round(t.runtime_min * (1 - pct / 100))) : null
  const canPlay = inApp.length > 0
  const requestShown = requestOf(t)

  // The file whose audio tracks are listed: the variant that plays by default, or the only one there is.
  const options = audio.data?.options ?? []
  const trackOption = t.kind === 'channel' ? undefined : options.find((o) => o.variant_id === audio.data?.automatic_variant_id) ?? (options.length === 1 ? options[0] : undefined)
  const tracks = trackOption?.audio_tracks ?? []
  const shownTrack = pickedTrack ?? initialTrack(tracks, trackOption?.preferred_audio_track)
  const playing = tracks.find((track) => track.index === shownTrack)
  const playLabel = canPlay ? (t.kind !== 'channel' && audio.data && !audio.data.options.length ? 'Choose audio / source' : left ? `Resume · ${left} min left` : 'Play') : 'Open in app'

  const primary = () => {
    if (canPlay) {
      if (t.kind === 'channel') return void play(t)
      // A picked track is an explicit choice of that file's audio, so it needs no second confirmation.
      if (trackOption && pickedTrack !== null) return void play(t, null, { variantId: trackOption.variant_id, audioTrack: pickedTrack })
      if (audio.data?.automatic_variant_id) return void play(t, null, { variantId: audio.data.automatic_variant_id })
      // Never held for an audio choice (the owner's decision: "if one Spanish one sneaks through, my ears
      // aren't going to bleed"): an older server that names no automatic source still plays its best one.
      const best = options.find((o) => o.preferred_audio_available) ?? options[0]
      if (best) return void play(t, null, { variantId: best.variant_id, allowUnverifiedAudio: true })
      setPicker('play'); return
    }
    if (handoff.length === 1) return void play(t, handoff[0])
    setPicker('play')
  }
  // Picking a source is the choice: it plays, with no second "play this anyway?".
  const chooseVariant = (option: PlaybackOption) => {
    setPicker(null); void play(t, null, { variantId: option.variant_id, allowUnverifiedAudio: true, audioTrack: option.variant_id === trackOption?.variant_id ? pickedTrack : null })
  }
  const choose = (s: SourceBadge) => { setPicker(null); void play(t, s) }
  const sendRequest = (picked?: number[]) => {
    const ref = tmdbRef(t.id)
    if (!ref || createRequest.isPending) return
    setSeasonPick(null)
    createRequest.mutate({ ...ref, seasons: picked?.length ? [...picked].sort((a, b) => a - b) : null }, {
      onSuccess: (r) => {
        // The button is about to be replaced by the request state; move on before it goes.
        if (getCurrentFocusKey() === 'title:request') void setFocus('title:watchlist')
        toast(r.state === 'pending_approval' ? 'Requested. Waiting for approval.' : 'Requested.', 'success')
      },
      onError: (e) => toast(e.message, 'error'),
    })
  }
  const startRequest = () => { if ((t.kind === 'show' || t.kind === 'series') && (t.request_seasons?.length ?? 0) > 1) setSeasonPick([]); else sendRequest() }
  // The library has part of this series: the request is for the seasons it is missing.
  const missing = t.request_missing ? t.request_seasons ?? [] : []
  const requestLabel = missing.length === 1 ? `Request season ${missing[0]}` : missing.length ? 'Request missing seasons'
    : t.request_state === 'declined' || t.request_state === 'failed' ? 'Request again' : 'Request'
  const toggleFavorite = () => toggleFav.mutate({ kind: t.kind, id: t.id, on: !t.is_favorite })
  const openTrailer = () => {
    // The TV app loads only this server's pages, so a new YouTube tab opens nowhere there. It is asked to
    // play `trailer:<id>` instead: the server answers with a hand-off to the TV's YouTube app, at the
    // trailer itself when TMDB knows it. A refusal is said by handOff. An episode's trailer is its show's.
    if (inShell()) return void handOff({ v: 1, kind: 'title', id: `trailer:${showId ?? t.id}` })
    const name = parent?.show?.title ?? t.title
    window.open(`https://www.youtube.com/results?search_query=${encodeURIComponent(`${name} ${t.year ?? ''} trailer`)}`, '_blank', 'noopener')
  }
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
  const refresh = () => { for (const key of ['home', 'search', 'browse', 'title', 'title-activity', 'history']) void qc.invalidateQueries({ queryKey: [key] }) }
  // Watched is a stop at the very end; unwatched (and Start over) a stop at the start, which clears the resume point.
  const report = (atEnd: boolean) => {
    const dur = (t.runtime_min ?? 0) * 60_000
    return ok(api.POST('/playback/progress', { body: { id: t.id, position_ms: atEnd ? dur : 0, duration_ms: dur || null, state: 'stopped' } }))
  }
  const toggleWatched = async () => {
    try {
      await report(!watched)
      refresh()
      toast(watched ? 'Marked as unwatched.' : 'Marked as watched.', 'success')
    } catch { toast('Could not save watched status. Try again.', 'error') }
  }
  const startOver = async () => {
    try { await report(false) } catch { /* the worst case is playing from the resume point */ }
    refresh()
    primary()
  }

  // The next episode of this one's season, when the strip lists it.
  const at = t.kind === 'episode' ? episodes.findIndex((e) => e.id === t.id) : -1
  const next = at >= 0 ? episodes[at + 1] : undefined
  const openEpisode = (e: EpisodeTile) => {
    if (e.id === t.id) return
    // Moving along a season replaces this episode, so Back still leads where it would have from here.
    if (t.kind === 'episode') go(titlePath(e.id), { replace: true, state: { from } })
    else go(titlePath(e.id))
  }

  // The ⋯ menu: what is seldom used. The pickers it opens are the ones the page always had.
  const menu: MenuItem[] = [
    ...(canPlay && mayStartRoom ? [{ key: 'together', icon: 'people', label: startingRoom ? 'Starting a room…' : 'Watch together', onEnter: () => void watchTogether(), testId: 'title-together', disabled: startingRoom, group: 1 } as MenuItem] : []),
    ...(next ? [{ key: 'next', icon: 'next', label: 'Play next episode', onEnter: () => void play(next), testId: 'title-next', group: 1 } as MenuItem] : []),
    ...(t.kind !== 'channel' ? [{ key: 'fav', icon: 'heart', label: t.is_favorite ? 'Remove from favorites' : 'Add to favorites', onEnter: toggleFavorite, testId: 'title-fav', pressed: !!t.is_favorite, group: 1 } as MenuItem] : []),
    ...(choosable && tracks.length ? [{ key: 'audio', icon: 'audio', label: `Audio · ${playing ? trackLabel(playing) : 'Choose'}`, onEnter: () => setPicker('play'), testId: 'title-audio', group: 2 } as MenuItem] : []),
    ...(choosable ? [{ key: 'source', icon: 'version', label: `Version · ${trackOption?.source_name ?? (inApp.length === 1 ? inApp[0]!.name : 'Choose')}`, onEnter: () => setPicker('play'), testId: 'title-source', group: 2 } as MenuItem] : []),
    ...(t.kind !== 'channel' ? [{ key: 'trailer', icon: 'trailer', label: 'Show trailer', onEnter: openTrailer, testId: 'title-trailer', group: 2 } as MenuItem] : []),
    ...(canRequest && (canPlay || handoff.length > 0) ? [{ key: 'request', icon: 'request', label: requestLabel, onEnter: startRequest, testId: 'title-request', group: 2 } as MenuItem] : []),
  ]
  const runMenu = (item: MenuItem) => {
    // The favourite toggles in place, so the list then says what it has become; anything else closes it first.
    if (item.key === 'fav') return item.onEnter()
    setMore(false)
    item.onEnter()
  }
  const firstMenuKey = `title:menu:${menu[0]?.key ?? 'close'}`
  const menuList = (
    <FocusGroup focusKey="title:menu" boundary className="tp-menu-list" role="menu" label="More actions" data-testid="title-more-menu">
      {menu.map((item, i) => <span key={item.key} style={{ display: 'contents' }}>
        {i > 0 && item.group !== menu[i - 1]!.group && <hr className="tp-menu-sep" />}
        <Focusable focusKey={`title:menu:${item.key}`} className="tp-menu-item" role="menuitem" label={item.label} aria-pressed={item.pressed} disabled={item.disabled}
          data-testid={item.testId} onEnter={() => runMenu(item)}>
          <TitleIcon name={item.icon} filled={item.key === 'fav' && item.pressed ? true : undefined} /><span>{item.label}</span>
        </Focusable>
      </span>)}
      {phone && <Button focusKey="title:menu:close" ghost onEnter={() => setMore(false)}>Close</Button>}
    </FocusGroup>
  )

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

  // The picture: an episode's own still, else the backdrop; a portrait poster keeps its shape.
  const art = t.backdrop ?? t.poster
  const shape = t.backdrop ? 'wide' : 'poster'
  const showTitle = nested ? parent?.show?.title : undefined
  const episodeCount = parent?.episode_count
  const date = formatDate(t.release_date)
  const quality = inApp.find((s) => s.quality)?.quality
  const spec: Array<[string, string]> = []
  if (quality) spec.push(['Video', quality])
  if (playing) spec.push(['Audio', trackLabel(playing)])
  if (t.genres?.length && !nested) spec.push(['Genre', t.genres.slice(0, 3).join(', ')])
  const cast = t.cast?.length ? t.cast : nested ? showQ.data?.cast ?? [] : []
  const stripTitle = seasons.find((s) => s.id === stripSeason)?.title ?? (t.kind === 'season' ? t.title : undefined)
  const showLink = showId && showTitle ? (
    <Focusable focusKey="title:show" className="tp-show" role="link" label={`${showTitle}, open the show`} onEnter={() => go(titlePath(showId))} data-testid="title-show-link">
      <span>{showTitle}</span><TitleIcon name="chev" />
    </Focusable>
  ) : null
  const crumbs = (t.kind === 'episode' && (parent?.season || parent?.episode_number != null)) || (t.kind === 'season' && episodes.length > 0)

  return (
    <div data-testid="title-detail" className="tp" data-kind={t.kind}>
      <div className="tp-bg" aria-hidden="true">
        <Crossfade src={parent?.show?.backdrop ?? t.backdrop ?? t.poster} />
        <div className="tp-veil" />
      </div>
      <div className="tp-page">
        <div className="tp-hero">
          <div className="tp-pic">
            <div className="tp-pic-art" data-shape={shape}>
              <SafeImg className="art" src={art} alt="" fallback={<div className="grid place-items-center h-full p-4 text-center heading">{showTitle ?? t.title}</div>} />
              {pct > 0 && pct < 100 && <ProgressBar pct={pct} className="tp-pic-bar" />}
            </div>
            {(left || watched) && <p className="tp-pic-cap">{watched ? 'Watched' : `${left} min left`}</p>}
          </div>

          <div className="tp-info">
            {showLink ? <h1 className="tp-h1">{showLink}</h1> : <h1 className="tp-show"><span>{t.title}</span></h1>}
            {t.kind === 'episode' && <p className="tp-sub">{parent?.episode_title ?? t.title}</p>}
            {t.kind === 'season' && showTitle && <p className="tp-sub">{t.title}</p>}
            {crumbs && (
              <FocusGroup focusKey="title:crumbs" className="tp-se">
                {t.kind === 'episode' && parent?.season && (
                  <Focusable focusKey="title:season" className="tp-glass-chip" role="link" label={`${parent.season.title}, open the season`} onEnter={() => go(titlePath(parent.season!.id))} data-testid="title-season-link">
                    {parent.season.title}
                  </Focusable>
                )}
                {t.kind === 'episode' && parent?.episode_number != null && <span>Episode {parent.episode_number}{episodeCount ? ` of ${episodeCount}` : ''}</span>}
                {t.kind === 'season' && <span>{episodes.length} episode{episodes.length === 1 ? '' : 's'}</span>}
              </FocusGroup>
            )}
            <p className="tp-meta">
              {t.content_rating && <span className="tp-rating" aria-label={`Rated ${t.content_rating}`}>{t.content_rating}</span>}
              {date ? <span>{date}</span> : t.year ? <span>{t.year}</span> : null}
              {t.runtime_min ? <span>{t.runtime_min} min</span> : null}
              {t.rating ? <span className="tp-star"><b>★</b> {t.rating.toFixed(1)}</span> : null}
            </p>

            <FocusGroup focusKey="title:actions" className="tp-actions" preferred="title:play">
              {(canPlay || handoff.length > 0) && (
                <Button focusKey="title:play" primary className="tp-play" onEnter={primary} testId="title-play" label={phone && left ? playLabel : undefined}>
                  <TitleIcon name="play" /><span>{phone && left ? 'Resume' : playLabel}</span>
                </Button>
              )}
              {canRequest && !canPlay && !handoff.length && (
                <Button focusKey="title:request" primary className="tp-play" onEnter={startRequest} testId="title-request"><TitleIcon name="request" /><span>{requestLabel}</span></Button>
              )}
              {canPlay && left && t.kind !== 'channel' && <Glass focusKey="title:restart" icon="restart" label="Start over" onEnter={() => void startOver()} testId="title-restart" />}
              {canPlay && t.kind !== 'channel' && (
                <Glass focusKey="title:watched" icon="watched" label={watched ? 'Mark unwatched' : 'Mark watched'} pressed={watched} onEnter={() => void toggleWatched()} testId="title-watched" />
              )}
              {t.kind !== 'channel' && (
                <Glass focusKey="title:watchlist" icon="list" label="My List" tip={onWatchlist ? 'On My List' : 'Add to My List'} pressed={onWatchlist}
                  onEnter={() => toggleWatch.mutate({ id: t.id, on: !onWatchlist })} testId="title-watchlist" />
              )}
              {menu.length > 0 && <Glass focusKey="title:more" icon="dots" label="More actions" tip="More" buttonRef={moreButton} expanded={more} onEnter={() => setMore(true)} testId="title-more" />}
            </FocusGroup>

            {/* A finished earlier request says nothing about the seasons still missing. */}
            {requestShown && !(t.request_missing && !requestOpen) && (
              <div className="tp-status">
                <RequestStatus testId="title-request-state" progress={request?.progress ?? requestShown}>
                  {requestShown.phase === 'available' ? 'Not in your library yet: it appears after the next library sync.' : null}
                </RequestStatus>
              </div>
            )}
            {t.overview && <p className="tp-overview">{t.overview}</p>}
            {spec.length > 0 && <dl className="tp-spec" data-testid="title-spec">{spec.map(([k, v]) => <div key={k} style={{ display: 'contents' }}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>}
          </div>
        </div>

        {(seasons.length > 0 || episodes.length > 0) && (
          <section className="tp-section" aria-label="Seasons and episodes" data-testid="episodes">
            {seasons.length > 1 ? (
              <FocusGroup focusKey="title:seasons" className="tp-seasons">
                {/* A long-running series would fill the screen with chips: past a row's worth, one control opens the list. */}
                {seasons.length > MAX_SEASON_CHIPS
                  ? <CatalogPicker scope="title:season" label="Season" search={false} testId="season-picker" value={stripSeason ?? ''}
                      options={seasons.map((s) => ({ id: s.id, label: s.title }))} onChange={(picked) => setPickedSeason(picked === defaultSeason ? null : picked)} />
                  : seasons.map((s) => (
                    <Chip key={s.id} focusKey={`season:${s.id}`} pressed={stripSeason === s.id} onEnter={() => setPickedSeason(s.id === defaultSeason ? null : s.id)}>{s.title}</Chip>
                  ))}
                {episodes.length > 0 && <small className="tp-count">{episodes.length} episode{episodes.length === 1 ? '' : 's'}</small>}
              </FocusGroup>
            ) : <h2>{stripTitle ?? 'Episodes'}{episodes.length > 0 && <small>{episodes.length} episode{episodes.length === 1 ? '' : 's'}</small>}</h2>}
            <FocusGroup focusKey="title:episodes" className="tp-row" remember preferred={episodes.some((e) => e.id === t.id) ? `episodes::${t.id}` : undefined}>
              {episodes.map((e) => <EpisodeCard key={e.id} e={e} current={e.id === t.id} onOpen={() => openEpisode(e)} />)}
              {!episodes.length && <p className="muted">{fetchSeason && seasonQ.isPending ? 'Loading episodes…' : 'No episodes listed.'}</p>}
            </FocusGroup>
          </section>
        )}

        <ActivityRow entries={activity.data?.entries ?? []} />
        <CastRow cast={cast} onOpen={(name) => go(`/search?q=${encodeURIComponent(name)}`)} />
        <SimilarRow tiles={similar.data?.tiles ?? []} />
        <div className="tp-attribution"><Attribution text={t.attribution || 'Availability data by JustWatch'} /></div>
      </div>

      {phone ? <Modal open={more} onClose={() => setMore(false)} title={showTitle ?? t.title} firstFocusKey={firstMenuKey} variant="sheet">{menuList}</Modal>
        : more && <Popover anchor={moreButton} firstFocusKey={firstMenuKey} onClose={() => setMore(false)} className="tp-menu">{menuList}</Popover>}

      <Modal open={picker !== null} onClose={() => setPicker(null)} title={canPlay ? 'Audio & version' : 'Open in'} firstFocusKey="pick:cancel" variant="sheet">
        <div className="flex flex-col gap-2">
          {canPlay ? <>
            {audioChips}
            {tracks.length > 0 && <h2 className="label">Version</h2>}
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
          <Button focusKey="pick:cancel" ghost onEnter={() => setPicker(null)}>Cancel</Button>
        </div>
      </Modal>

      <ShareSheet room={sharing ? room?.room ?? null : null} onClose={() => setSharing(false)} onStart={() => room && go(`/room/${room.room.code}?start=1`)} />

      <Modal open={seasonPick !== null} onClose={() => setSeasonPick(null)} title={missing.length ? `Request the rest of ${t.title}` : `Request ${t.title}`} firstFocusKey="reqpick:all">
        <div className="flex flex-col gap-3" data-testid="request-seasons">
          <Button focusKey="reqpick:all" primary onEnter={() => sendRequest()}>{missing.length ? 'All missing seasons' : 'All seasons'}</Button>
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
