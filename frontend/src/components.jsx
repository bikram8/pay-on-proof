import { useEffect, useState } from 'react'
import { fetchImageUrl } from './api.js'

const STATUS_TONE = {
  PAID_OUT: 'good', COMPLETED: 'good', APPROVED: 'good', AUTHORIZED: 'info', ESCROWED: 'info',
  CAPTURED: 'warn', HUMAN_REVIEW: 'warn', FUNDED_PENDING: 'muted', PENDING: 'muted',
  REJECTED: 'bad', CANCELLED: 'muted', REVIEW: 'warn', APPROVE: 'good', REJECT: 'bad',
  OK: 'good', ALLOWED: 'good', BLOCKED: 'bad', ERROR: 'bad', UNVERIFIED: 'warn',
}

const STATUS_LABEL = {
  FUNDED_PENDING: 'Awaiting funding', AUTHORIZED: 'Funds held', ESCROWED: 'In escrow',
  HUMAN_REVIEW: 'Needs review', CAPTURED: 'Payout pending', PAID_OUT: 'Paid',
}

export function Status({ value }) {
  if (!value) return null
  const label = STATUS_LABEL[value] || value.replaceAll('_', ' ').toLowerCase()
  return <span className={`badge ${STATUS_TONE[value] || 'muted'}`}>{label}</span>
}

// PayPal payout item statuses: did the worker actually receive the money?
const PAYOUT = {
  SUCCESS: ['good', 'Received', 'PayPal confirmed the worker received the money.'],
  PENDING: ['info', 'Sending', 'PayPal is processing the payout.'],
  ONHOLD: ['warn', 'On hold', 'PayPal is reviewing this payout.'],
  UNCLAIMED: ['bad', 'Unclaimed', 'No PayPal account uses this email yet. The worker must sign up with it within 30 days, or the money is returned.'],
  FAILED: ['bad', 'Failed', 'The payout failed. The money stays with the platform.'],
  RETURNED: ['bad', 'Returned', 'Not claimed in time; the money was returned to the platform.'],
  BLOCKED: ['bad', 'Blocked', 'PayPal blocked this payout.'],
  DENIED: ['bad', 'Denied', 'PayPal denied this payout.'],
  CANCELED: ['bad', 'Cancelled', 'The payout was cancelled.'],
  REFUNDED: ['bad', 'Refunded', 'The worker refunded this payout.'],
  REVERSED: ['bad', 'Reversed', 'This payout was reversed.'],
}
export const PAYOUT_FINAL = new Set(['SUCCESS', 'FAILED', 'RETURNED', 'BLOCKED', 'DENIED', 'CANCELED', 'REFUNDED', 'REVERSED'])

export function payoutHelp(status) {
  return PAYOUT[status]?.[2] || ''
}

export function PayoutStatus({ value }) {
  if (!value) return null
  const [tone, label, help] = PAYOUT[value] || ['muted', value.toLowerCase(), '']
  return <span className={`badge ${tone}`} title={help}>{label}</span>
}

/** <img> for admin-only images (needs the auth header, so it is fetched as a blob). */
export function AuthImage({ path, alt }) {
  const [src, setSrc] = useState(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let url
    setSrc(null); setFailed(false)
    fetchImageUrl(path).then(u => { url = u; setSrc(u) }).catch(() => setFailed(true))
    return () => url && URL.revokeObjectURL(url)
  }, [path])
  if (failed) return <div className="img-placeholder">Image file missing</div>
  if (!src) return <div className="img-placeholder">Loading…</div>
  return <a href={src} target="_blank" rel="noreferrer"><img src={src} alt={alt} /></a>
}

export function ErrorBox({ error }) {
  if (!error) return null
  return <div className="alert bad" role="alert">{error.message || String(error)}</div>
}

export function Empty({ title, children }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <p>{children}</p>}
    </div>
  )
}

/** Small transient message. Usage: const [toast, show] = useToast(); ... {toast} */
export function useToast() {
  const [msg, setMsg] = useState(null)
  useEffect(() => {
    if (!msg) return
    const id = setTimeout(() => setMsg(null), 4000)
    return () => clearTimeout(id)
  }, [msg])
  const node = msg && <div className={`toast ${msg.tone}`} role="status">{msg.text}</div>
  return [node, (text, tone = 'good') => setMsg({ text, tone })]
}

/** Prefers-color-scheme aware AG Grid theme class. */
export function useGridTheme() {
  const query = '(prefers-color-scheme: dark)'
  const [dark, setDark] = useState(() => window.matchMedia?.(query).matches)
  useEffect(() => {
    const mq = window.matchMedia?.(query)
    if (!mq) return
    const on = e => setDark(e.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  return dark ? 'ag-theme-quartz-dark' : 'ag-theme-quartz'
}

export function formatTime(iso) {
  if (!iso) return ''
  // Backend timestamps are naive UTC.
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z')
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}
