import type { CastMember, EpisodeTile, Tile, TitleActivityEntry } from './types'
import { Focusable } from '../../nav/Focusable'
import { SafeImg, initialsOf } from '../../components/SafeImg'
import { Avatar } from '../../components/Avatar'
import { MediaTile } from '../../components/Tile'
import { ProgressBar } from '../../components/ui'
import { HRow, Section, StillCard, nameGradient as gradient } from '../../ds'

/** "S2 · E1" without the parts that are not known. */
export function seLabel(season?: number | null, episode?: number | null): string {
  return [season != null ? `S${season}` : null, episode != null ? `E${episode}` : null].filter(Boolean).join(' · ')
}

/** "3 days ago", "last week": a viewer's sense of when, not a timestamp. */
export function ago(iso: string, now = Date.now()): string {
  const minutes = Math.max(0, Math.round((now - Date.parse(iso)) / 60_000))
  if (minutes < 2) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return hours === 1 ? 'an hour ago' : `${hours} hours ago`
  const days = Math.round(hours / 24)
  if (days === 1) return 'yesterday'
  if (days < 7) return `${days} days ago`
  if (days < 14) return 'last week'
  if (days < 60) return `${Math.round(days / 7)} weeks ago`
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

/** What one household member did, in a few words. */
export function activityText(e: TitleActivityEntry): string {
  const se = seLabel(e.season_number, e.episode_number)
  if (e.action === 'watching') return [se ? `Watching ${se}` : 'Watching', e.minutes_left ? `${e.minutes_left} min left` : null].filter(Boolean).join(' · ')
  if (e.action === 'listed') return `Added to My List · ${ago(e.at)}`
  const what = e.season_finished && e.season_number != null ? `Finished Season ${e.season_number}` : se ? `Watched ${se}` : 'Finished'
  return `${what} · ${ago(e.at)}`
}

export function ActivityRow({ entries }: { entries: TitleActivityEntry[] }) {
  if (!entries.length) return null
  return (
    <Section className="tp-section" title="Activity" testId="title-activity">
      <div className="tp-acts">
        {entries.map((e) => {
          const name = e.me ? 'You' : e.name
          return (
            <div key={e.profile_id} className="tp-act ds-panel" data-testid="activity-entry">
              {e.avatar || e.picture ? <Avatar name={e.name} avatar={e.avatar} picture={e.picture} size={2.6} />
                : <span className="tp-act-face" aria-hidden="true" style={{ background: e.me ? 'linear-gradient(135deg, var(--t-accent), color-mix(in srgb, var(--t-accent) 60%, #b3360f))' : gradient(e.name) }}>{name.slice(0, 1).toUpperCase()}</span>}
              <div className="tp-act-text">
                <b>{name}</b>
                <small>{activityText(e)}</small>
                {e.action === 'watching' && e.progress_pct != null && <ProgressBar label={`Viewing progress · ${name}`} pct={e.progress_pct} className="tp-act-bar" />}
              </div>
            </div>
          )
        })}
      </div>
    </Section>
  )
}

/** Cast & Crew. OK on a face searches for that person. */
export function CastRow({ cast, onOpen }: { cast: CastMember[]; onOpen: (name: string) => void }) {
  const people = cast.filter((c): c is CastMember & { name: string } => !!c.name).slice(0, 20)
  if (!people.length) return null
  return (
    <Section className="tp-section" title="Cast & Crew" label="Cast and crew" testId="title-cast">
      <HRow focusKey="title:cast">
        {people.map((c, i) => (
          <Focusable key={`${c.name}:${i}`} focusKey={`cast:${i}`} className="tp-cast" role="link" label={c.character ? `${c.name} as ${c.character}` : c.name} onEnter={() => onOpen(c.name)}>
            <div className="tp-cast-photo">
              <SafeImg className="art" src={c.photo} alt="" loading="lazy" fallback={<span className="tp-cast-initials" style={{ background: gradient(c.name) }}>{initialsOf(c.name)}</span>} />
            </div>
            <b>{c.name}</b>
            {c.character && <small>{c.character}</small>}
          </Focusable>
        ))}
      </HRow>
    </Section>
  )
}

export function SimilarRow({ tiles }: { tiles: Tile[] }) {
  if (!tiles.length) return null
  return (
    <Section className="tp-section" title="More like this" testId="title-similar">
      <HRow focusKey="title:similar" className="tp-similar-row">
        {tiles.map(t => <MediaTile key={t.id} tile={t} scope="similar" mark="availability" captionBelow />)}
      </HRow>
    </Section>
  )
}

/** "E4 · In the Rubble" names the number apart; the card shows "4 · In the Rubble". */
export function episodeName(e: Pick<EpisodeTile, 'title' | 'episode_number'>): string {
  return e.episode_number != null ? e.title.replace(new RegExp(`^E${e.episode_number} · `), '') : e.title
}

export function EpisodeCard({ e, current, onOpen }: { e: EpisodeTile; current: boolean; onOpen: () => void }) {
  const pct = e.progress_pct ?? 0
  const name = episodeName(e)
  const number = e.episode_number != null && !/^\d+\./.test(name) ? `${e.episode_number} · ` : ''
  return (
    <StillCard focusKey={`episodes::${e.id}`} className="tp-ep" current={current} testId="episode" onEnter={onOpen}
      label={`${number}${name}${e.runtime_min ? `, ${e.runtime_min} min` : ''}${pct >= 100 ? ', watched' : ''}`}
      image={e.backdrop ?? e.poster} fallbackName={name} progressPct={pct} watched={pct >= 100}
      title={`${number}${name}`} meta={e.runtime_min ? `${e.runtime_min} min` : undefined} />
  )
}
