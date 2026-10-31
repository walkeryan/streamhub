import type { ReactNode } from 'react'
import { Focusable } from '../nav/Focusable'
import { SafeImg, Initials } from './SafeImg'
import { ProgressBar } from './ui'

/** A single remote target: real artwork above, persistent programme metadata below. */
export function FeatureCard({ focusKey, label, title, eyebrow, art, logo, fallbackName, badge, progress, onEnter, onFocus, testId, children, role = 'link' }: {
  focusKey: string; label: string; title: string; eyebrow: string; art?: string | null; logo?: string | null;
  fallbackName: string; badge?: string; progress?: number | null; onEnter: () => void; onFocus?: () => void; testId?: string; children?: ReactNode; role?: 'link' | 'button';
}) {
  const fallback = logo ? <SafeImg className="feature-logo" src={logo} alt="" loading="lazy" fallback={<Initials name={fallbackName} />} /> : <Initials name={fallbackName} />
  return <Focusable focusKey={focusKey} className="feature-card fx-flat" role={role} label={label} onEnter={onEnter} onFocus={onFocus} data-testid={testId}>
    <div className="feature-art">
      <SafeImg className="feature-image" src={art} alt="" loading="lazy" decoding="async" fallback={fallback} />
      {badge && <span className="feature-status">{badge}</span>}
      {typeof progress === 'number' && <div className="feature-progress"><ProgressBar pct={progress} /></div>}
    </div>
    <div className="feature-copy">
      <p className="label clamp2">{eyebrow}</p>
      <h3 className="heading clamp2">{title}</h3>
      {children}
    </div>
  </Focusable>
}
