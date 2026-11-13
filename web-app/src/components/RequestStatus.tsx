import { Icon } from './Icon'
import { cn } from '../lib/util'
import { PHASE_LABEL, etaText, phaseTone } from '../lib/requests'
import type { RequestPhase, RequestProgress, TileRequestProgress } from '../api/types'

const ICON: Record<RequestPhase, string> = {
  requested: 'bookmark', approved: 'check', searching: 'search', downloading: 'download', importing: 'refresh',
  partially_available: 'check', available: 'check', declined: 'x', failed: 'x', stalled: 'pause', unknown: 'info',
}
const TONE = { ok: 'badge-ok', warn: 'badge-warn', plain: '' } as const

/** The phase as a chip. Its words carry the meaning; the icon and the tone only repeat it. */
function PhaseChip({ phase, compact, children }: { phase: RequestPhase; compact?: boolean; children?: string }) {
  return (
    <span className={cn('badge request-chip', TONE[phaseTone(phase)])} data-testid="request-phase" data-phase={phase}>
      {!compact && <Icon name={ICON[phase]} size={12} />}
      <span>{PHASE_LABEL[phase]}{children}</span>
    </span>
  )
}

/**
 * How far a download is. With a percentage it is a bar that fills; without one (the sizes are not
 * known) it is a bar that only says something is moving, and stands still for reduced motion.
 */
function DownloadBar({ percent, text, of, className }: { percent: number | null | undefined; text: string; of?: string; className?: string }) {
  const known = typeof percent === 'number'
  return (
    <div className={cn('request-bar', className)} role="progressbar" aria-label={of ? `Download progress: ${of}` : 'Download progress'} aria-valuemin={0} aria-valuemax={100}
      aria-valuenow={known ? percent : undefined} aria-valuetext={text} data-indeterminate={known ? undefined : 'true'} data-testid="request-bar">
      <i style={known ? { width: `${Math.min(100, Math.max(0, percent))}%` } : undefined} />
    </div>
  )
}

/** What is left to say beside the chip: the server's detail, and for an unknown status what was last known. */
function secondaryText(p: RequestProgress | TileRequestProgress): string | null {
  const detail = 'detail' in p ? p.detail : null
  const last = 'last_phase' in p && p.phase === 'unknown' && p.last_phase ? `Last known: ${PHASE_LABEL[p.last_phase].toLowerCase()}.` : null
  return [detail, last].filter(Boolean).join(' ') || null
}

/**
 * A request's status, wherever a request is shown in full (a Requests row, the title page): the phase chip,
 * what the server adds to it, and while downloading a bar with the percentage and the time left.
 * Takes the whole progress of a request, or the phase and percentage a tile carries. `of` names the title
 * for the bar where several are on one screen; `children` is a further remark for beside the chip.
 */
export function RequestStatus({ progress, of, testId, children }: { progress: RequestProgress | TileRequestProgress; of?: string; testId?: string; children?: string | null }) {
  const downloading = progress.phase === 'downloading'
  const percent = downloading ? progress.percent : null
  const eta = downloading && 'eta' in progress ? etaText(progress) : null
  const amount = typeof percent === 'number' ? `${percent}%` : null
  const secondary = [secondaryText(progress), children].filter(Boolean).join(' ')
  return (
    <div className="request-status" data-testid={testId} data-phase={progress.phase}>
      {/* Only the phase is announced when it changes; a percentage ticking up is not. */}
      <p className="request-status-line" role="status">
        <PhaseChip phase={progress.phase} />
        {secondary && <span className="label" data-testid="request-detail">{secondary}</span>}
      </p>
      {downloading && (
        <div className="request-progress">
          <DownloadBar percent={percent} of={of} text={[amount ?? 'Amount not reported', eta].filter(Boolean).join(', ')} />
          {(amount || eta) && <span className="label request-progress-text" data-testid="request-progress-text">{[amount, eta].filter(Boolean).join(' · ')}</span>}
        </div>
      )}
    </div>
  )
}

/**
 * The same status for the corner of a poster: a small chip, and while downloading a thin bar along the
 * tile's bottom edge. Place it directly inside the tile (a positioned box); it brings its own corner.
 */
export function RequestTileStatus({ progress, of }: { progress: TileRequestProgress; of?: string }) {
  const downloading = progress.phase === 'downloading'
  const amount = downloading && typeof progress.percent === 'number' ? `${progress.percent}%` : null
  return (
    <>
      <div className="tile-badges"><PhaseChip phase={progress.phase} compact>{amount ? ` ${amount}` : undefined}</PhaseChip></div>
      {downloading && <DownloadBar className="request-tile-bar" percent={progress.percent} of={of} text={amount ?? 'Amount not reported'} />}
    </>
  )
}
