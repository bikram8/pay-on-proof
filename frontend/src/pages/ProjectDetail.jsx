import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, money, notifyChanged, workerLink } from '../api.js'
import { ErrorBox, PAYOUT_FINAL, PayoutStatus, Status, formatTime, payoutHelp, useToast } from '../components.jsx'

export default function ProjectDetail() {
  const { id } = useParams()
  const [p, setP] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const [approveUrl, setApproveUrl] = useState(null)
  const [toast, showToast] = useToast()

  const load = useCallback(() => api(`/projects/${id}`).then(setP).catch(setError), [id])
  useEffect(() => { load() }, [load])

  // While waiting for the funder to approve on PayPal, watch for the status change.
  useEffect(() => {
    if (p?.status !== 'FUNDED_PENDING' || !p.paypal_order_id) return
    const t = setInterval(load, 4000)
    return () => clearInterval(t)
  }, [p?.status, p?.paypal_order_id, load])

  // Ask PayPal whether sent payouts reached the worker, until each one is final.
  const payoutsPending = p?.milestones.some(m => m.status === 'PAID_OUT' && !PAYOUT_FINAL.has(m.payout_status))
  useEffect(() => {
    if (!payoutsPending) return
    const sync = () => api(`/projects/${id}/sync-payouts`, { method: 'POST' }).then(setP).catch(() => {})
    sync()
    const t = setInterval(sync, 15000)
    return () => clearInterval(t)
  }, [payoutsPending, id])

  async function fund() {
    setBusy('fund'); setError(null)
    try {
      const r = await api(`/projects/${id}/fund`, { method: 'POST' })
      setApproveUrl(r.approve_url)
      window.open(r.approve_url, '_blank', 'noopener')
      load()
    } catch (err) { setError(err) } finally { setBusy(null) }
  }

  async function cancel() {
    if (!confirm('Cancel this project and return all unreleased money to the funder? This cannot be undone.')) return
    setBusy('cancel'); setError(null)
    try {
      setP(await api(`/projects/${id}/cancel`, { method: 'POST' }))
      showToast('Project cancelled; unreleased funds returned.', 'muted')
      notifyChanged()
    } catch (err) { setError(err) } finally { setBusy(null) }
  }

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(workerLink(p))
      showToast('Worker link copied')
    } catch {
      prompt('Copy this worker link:', workerLink(p))
    }
  }

  if (!p) return <section><ErrorBox error={error} />{!error && <p className="muted">Loading…</p>}</section>

  const funded = ['AUTHORIZED', 'ESCROWED'].includes(p.status)
  const held = funded ? p.total_amount - p.paid_out : 0

  return (
    <section>
      <Link to="/projects" className="muted small-text">← Projects</Link>
      <div className="page-head">
        <div>
          <h1>{p.title} <Status value={p.status} /></h1>
          {p.description && <p className="muted">{p.description}</p>}
        </div>
        <div className="head-actions">
          {funded && <button className="ghost" onClick={copyLink}>Copy worker link</button>}
          {!['COMPLETED', 'CANCELLED'].includes(p.status) && (
            <button className="danger-ghost" onClick={cancel} disabled={busy}>Cancel project</button>
          )}
        </div>
      </div>

      <ErrorBox error={error} />

      {p.status === 'FUNDED_PENDING' && (
        <div className="card callout">
          <div>
            <h2>Fund this project</h2>
            <p className="muted">
              The funder approves {money(p.total_amount, p.currency)} on PayPal. The money is then held
              and released only as milestones are verified.
            </p>
            {approveUrl && (
              <p className="small-text">
                Waiting for approval… If the PayPal tab didn't open, <a href={approveUrl} target="_blank" rel="noreferrer">open it here</a>.
              </p>
            )}
          </div>
          <button className="primary paypal" onClick={fund} disabled={busy}>
            {busy === 'fund' ? 'Opening PayPal…' : approveUrl ? 'Open PayPal again' : 'Fund with PayPal'}
          </button>
        </div>
      )}

      <div className="stats">
        <Stat label="Total" value={money(p.total_amount, p.currency)} />
        <Stat label="Held in escrow" value={money(held, p.currency)} />
        <Stat label="Paid to workers" value={money(p.paid_out, p.currency)} />
        <Stat label="Mode" value={p.escrow_mode === 'AUTH_CAPTURE' ? 'Hold & capture' : 'Escrow & payout'} />
      </div>

      {funded && (
        <div className="card worker-link">
          <div>
            <h2>Worker upload link</h2>
            <p className="muted small-text">Send this to the worker. It only lets them submit proof photos for this project.</p>
            <code>{workerLink(p)}</code>
          </div>
          <button className="ghost" onClick={copyLink}>Copy</button>
        </div>
      )}

      <div className="card">
        <h2>Milestones</h2>
        <ol className="milestones">
          {p.milestones.map(m => (
            <li key={m.id}>
              <div className="ms-main">
                <strong>{m.title}</strong>
                <span className="muted small-text">Photo must show: {m.proof_description}</span>
                <span className="muted small-text">{m.worker_email} · clock started {formatTime(m.starts_at)}</span>
                {m.status === 'PAID_OUT' && m.payout_status && m.payout_status !== 'SUCCESS' && (
                  <span className="small-text warn-text">{payoutHelp(m.payout_status)}</span>
                )}
              </div>
              <div className="ms-side">
                <span className="amount">{money(m.amount, p.currency)}</span>
                <Status value={m.status} />
                {m.status === 'PAID_OUT' && <PayoutStatus value={m.payout_status} />}
                {['HUMAN_REVIEW', 'APPROVED', 'CAPTURED'].includes(m.status) && <Link to="/" className="small-text">Review →</Link>}
              </div>
            </li>
          ))}
        </ol>
      </div>

      <p className="muted small-text">
        PayPal order {p.paypal_order_id || '—'}{p.paypal_authorization_id && <> · authorization {p.paypal_authorization_id}</>}
        {p.site_lat != null && <> · site {p.site_lat.toFixed(4)}, {p.site_lon.toFixed(4)} (±{p.site_radius_m} m)</>}
      </p>
      {toast}
    </section>
  )
}

function Stat({ label, value }) {
  return (
    <div className="stat card">
      <span className="muted small-text">{label}</span>
      <strong>{value}</strong>
    </div>
  )
}
