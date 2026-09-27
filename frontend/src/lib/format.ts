const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** Section 12.5: dates as DD MMM YYYY. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return `${String(d.getDate()).padStart(2, '0')} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`
}

/** Indian digit grouping (e.g. 12,34,567). */
export function formatCount(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  return new Intl.NumberFormat('en-IN').format(n)
}

/** Rupees in crore with Indian grouping, e.g. 9.95 → "₹9.95 Cr". */
export function formatCrore(inr: number | null | undefined, digits = 2): string {
  if (inr === null || inr === undefined) return '—'
  const n = new Intl.NumberFormat('en-IN', { maximumFractionDigits: digits }).format(
    Math.abs(inr) / 1e7,
  )
  return `${inr < 0 ? '−' : ''}₹${n} Cr`
}

/** Rupees in lakh, e.g. 41,14,000 → "₹41.1 L". */
export function formatLakh(inr: number | null | undefined): string {
  if (inr === null || inr === undefined) return '—'
  const n = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 1 }).format(Math.abs(inr) / 1e5)
  return `${inr < 0 ? '−' : ''}₹${n} L`
}

export function formatPercent(share: number | null | undefined, digits = 1): string {
  if (share === null || share === undefined) return '—'
  return `${(share * 100).toFixed(digits)}%`
}
