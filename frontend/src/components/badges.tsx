import type { Confidence } from '../lib/api'
import type { LicenseClass } from '../map/licenseGuard'

const base =
  'inline-flex items-center whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] font-medium leading-none'

const CONFIDENCE_STYLE: Record<Confidence, string> = {
  HIGH: 'bg-ink text-paper',
  MEDIUM: 'border border-ink text-ink',
  LOW: 'border border-dashed border-muted text-muted',
  NONE: 'border border-dashed border-synthetic text-synthetic',
}

export function ConfidenceBadge({ value }: { value: Confidence }) {
  return (
    <span className={`${base} ${CONFIDENCE_STYLE[value]}`} title="Confidence">
      {value.toLowerCase()} confidence
    </span>
  )
}

const LICENSE_LABEL: Record<LicenseClass, string> = {
  OPEN: 'Open data',
  GOVERNMENT: 'Government',
  RESTRICTED_GOOGLE: 'Google — display only',
  RESTRICTED_COMMERCIAL: 'Commercial — restricted',
  SYNTHETIC: 'Synthetic — not real data',
}

export function LicenseBadge({ value }: { value: LicenseClass }) {
  const style =
    value === 'SYNTHETIC'
      ? 'bg-synthetic text-white'
      : value.startsWith('RESTRICTED')
        ? 'border border-warn text-warn'
        : 'border border-line text-muted'
  return <span className={`${base} ${style}`}>{LICENSE_LABEL[value]}</span>
}

const FRESHNESS_STYLE: Record<string, string> = {
  fresh: 'border border-line text-ink',
  stale: 'border border-warn text-warn',
  never: 'border border-dashed border-muted text-muted',
  'n/a': 'border border-line text-muted',
  live: 'border border-ink text-ink',
  off: 'border border-dashed border-muted text-muted',
  not_configured: 'border border-dashed border-muted text-muted',
}

const FRESHNESS_LABEL: Record<string, string> = {
  never: 'not ingested',
  live: 'live · not stored',
  off: 'switched off',
  not_configured: 'not configured',
}

export function FreshnessBadge({ value }: { value: string }) {
  const label = FRESHNESS_LABEL[value] ?? value
  return <span className={`${base} ${FRESHNESS_STYLE[value] ?? ''}`}>{label}</span>
}
