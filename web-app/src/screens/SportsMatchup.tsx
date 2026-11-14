import { useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
import type { components } from '../api/schema'
import { fmtTime } from '../lib/util'

type Event = components['schemas']['SportsEvent']
type Matchup = components['schemas']['SportsMatchup']
type Team = components['schemas']['MatchupTeam']

const hash = (text: string) => [...text].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7)
/** A team's colour; a team the scoreboard does not know gets a steady one made from its name. */
const colorOf = (team: Team) => team.color ?? `hsl(${hash(team.name) % 360} 55% 38%)`
const same = (a: string, b: string) => a.toLowerCase() === b.toLowerCase()

/** The two sides' colours, away then home. Two teams in one colour: home wears its alternate. */
export function sideColors(matchup: Matchup): [string, string] {
  const away = colorOf(matchup.away), home = colorOf(matchup.home)
  return [away, same(away, home) && matchup.home.alt_color ? matchup.home.alt_color : home]
}

const ranked = (team: Team) => (team.rank ? `number ${team.rank} ` : '')
const percent = (value: number) => `${Math.round(value)}%`

/** What a screen reader says of a matchup: "number 12 Brookfield Badgers versus Lakeview Kestrels…". */
export function matchupSpeech(event: Event, matchup: Matchup): string {
  const { away, home } = matchup
  const odds = !event.replay && matchup.win_probability
  return [
    `${ranked(away)}${away.name} versus ${ranked(home)}${home.name}`,
    !event.replay && matchup.status_detail,
    odds && `${odds.live ? 'win probability' : 'pregame win probability'} ${away.short_name} ${percent(odds.away)}, ${home.short_name} ${percent(odds.home)}`,
  ].filter(Boolean).join(', ')
}

/** The generated art over the two colours split on the diagonal; the art fades in once it has loaded. */
export function MatchupArt({ matchup, className }: { matchup: Matchup; className: string }) {
  const [away, home] = sideColors(matchup)
  const [loaded, setLoaded] = useState<string | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const src = matchup.art_url
  return <span className={className} data-testid="matchup-art" data-art={src && failed !== src ? 'image' : 'colors'} aria-hidden="true"
    style={{ '--away': away, '--home': home } as CSSProperties}>
    {src && failed !== src && <img src={src} alt="" decoding="async" data-loaded={loaded === src} onLoad={() => setLoaded(src)} onError={() => setFailed(src)} />}
  </span>
}

/** A team's name on one line in its colour, shrunk until it fits its half of the card. */
function FitName({ team, color }: { team: Team; color: string }) {
  const ref = useRef<HTMLSpanElement>(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const fit = () => {
      el.style.fontSize = ''
      let size = parseFloat(getComputedStyle(el).fontSize)
      while (el.scrollWidth > el.clientWidth + 0.5 && size > 9) { size -= 1; el.style.fontSize = `${size}px` }
    }
    fit()
    const resized = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => fit())
    resized?.observe(el.parentElement ?? el)
    // The web font can arrive after the first measure, wider than the fallback.
    void document.fonts?.ready.then(fit)
    return () => resized?.disconnect()
  }, [team.short_name])
  return <span ref={ref} className="matchup-name" data-testid="matchup-name" style={{ color }}>{team.short_name}</span>
}

function Side({ team, color, side }: { team: Team; color: string; side: 'away' | 'home' }) {
  const records = [team.record, team.conference_record].filter(Boolean).join(' · ')
  return <span className={`matchup-team matchup-${side}`} data-testid={`matchup-${side}`}>
    {team.rank ? <span className="matchup-rank" data-testid="matchup-rank" style={{ background: color }}>#{team.rank}</span> : null}
    <FitName team={team} color={color} />
    {records && <span className="matchup-records">{records}</span>}
  </span>
}

/** The inside of a Live now card for a football game: art, the teams, VS., the clock and the win probability. No scores. */
export function MatchupCard({ event, matchup, badge, channel }: { event: Event; matchup: Matchup; badge: React.ReactNode; channel: string }) {
  const [away, home] = sideColors(matchup)
  // A replay gives nothing of the game away: no clock and no odds, whatever arrives.
  const detail = event.replay ? null : matchup.status_detail
  const odds = event.replay ? null : matchup.win_probability
  return <>
    <MatchupArt matchup={matchup} className="matchup-art" />
    <span className="matchup-shade" aria-hidden="true" />
    <span className="matchup-top">
      {badge}
      <span className="matchup-channel truncate">{channel}</span>
    </span>
    <span className="matchup-teams">
      <Side team={matchup.away} color={away} side="away" />
      <span className="matchup-mid">
        <span className="matchup-vs">VS.</span>
        <span className="matchup-clock" data-testid="matchup-clock">{[fmtTime(event.start), detail].filter(Boolean).join(' · ')}</span>
      </span>
      <Side team={matchup.home} color={home} side="home" />
    </span>
    {odds && <>
      <span className="matchup-odds-label">{odds.live ? 'Win probability' : 'Pregame win probability'}</span>
      <span className="matchup-odds" data-testid="matchup-odds" style={{ background: home }}>
        <span className="matchup-odds-away" data-testid="matchup-odds-away" style={{ width: `${odds.away}%`, background: away }} />
        <b className="matchup-odds-a">{percent(odds.away)}</b>
        <b className="matchup-odds-h">{percent(odds.home)}</b>
      </span>
    </>}
  </>
}

/** A Coming up row's matchup: each side's ranking just before its name. */
export function MatchupTitle({ matchup }: { matchup: Matchup }) {
  const side = (team: Team) => <>{team.rank ? <span className="matchup-rank-inline">#{team.rank}</span> : null}{team.name}</>
  return <>{side(matchup.away)} at {side(matchup.home)}</>
}

export const matchupTitle = (matchup: Matchup) => `${matchup.away.rank ? `#${matchup.away.rank} ` : ''}${matchup.away.name} at ${matchup.home.rank ? `#${matchup.home.rank} ` : ''}${matchup.home.name}`
