import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, money, notifyChanged } from '../api.js'
import { AuthImage, Empty, ErrorBox, PayoutStatus, Status, formatTime, payoutHelp, useToast } from '../components.jsx'

const CHECK_LABELS = {
  metadata: 'Camera metadata', geofence: 'Location', duplicate: 'Not reused',
  timing: 'Taken after start', vision: 'Visual review',
}

export default function ReviewQueue() {
  const [items, setItems] = useState(null)
  const [error, setError] = useState(null)
  const [toast, showToast] = useToast()

  const load = useCallback(() => {
    api('/review-queue').then(setItems).catch(setError)
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 10000)
    return () => clearInterval(id)
  }, [load])

  function done(text, tone) {
    showToast(text, tone)
    load()
    notifyChanged()
  }

  return (
    <section>
      <div className="page-head">
        <div>
          <h1>Review queue</h1>
          <p className="muted">Proof the AI couldn't approve on its own, and payouts that need a retry.</p>
        </div>
      </div>
      <ErrorBox error={error} />
      {items && items.length === 0 && (
        <Empty title="Nothing waiting">
          New proof that needs your eyes will appear here. <Link to="/projects">View projects</Link>
        </Empty>
      )}
      <div className="queue">
        {items?.map(item => <ReviewCard key={item.milestone_id} item={item} onDone={done} />)}
      </div>
      {toast}
    </section>
  )
}

function ReviewCard({ item, onDone }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const sub = item.submission

  async function act(kind) {
    setBusy(kind); setError(null)
    try {
      if (kind === 'check') {
        const p = await api(`/projects/${item.project_id}/sync-payouts`, { method: 'POST' })
        const now = p.milestones.find(m => m.id === item.milestone_id)?.payout_status
        onDone(now === item.payout_status ? `Still ${now.toLowerCase()} at PayPal` : `Payout is now ${now.toLowerCase()}`,
          now === 'SUCCESS' ? 'good' : 'muted')
      } else if (kind === 'retry') {
        await api(`/milestones/${item.milestone_id}/release`, { method: 'POST' })
        onDone(`Paid ${money(item.amount, item.currency)} to ${item.worker_email}`)
      } else {
        const approve = kind === 'approve'
        const r = await api(`/milestones/${item.milestone_id}/review`, { body: { approve, reviewer_note: note } })
        onDone(approve ? `Paid ${money(item.amount, item.currency)} to ${item.worker_email}`
          : 'Rejected. The worker can submit a new photo.', approve ? 'good' : 'muted')
        return r
      }
    } catch (err) {
      setError(err)
      if (err.status === 502) onDone(err.message, 'bad')  // status may have moved to CAPTURED
    } finally {
      setBusy(null)
    }
  }

  return (
    <article className="card review-card">
      <div className="review-photo">
        {sub ? <AuthImage path={`/submissions/${sub.id}/image`} alt={`Proof for ${item.milestone_title}`} />
          : <div className="img-placeholder">No photo</div>}
      </div>
      <div className="review-body">
        <div className="review-title">
          <div>
            <Link to={`/projects/${item.project_id}`} className="muted small-text">{item.project_title}</Link>
            <h2>{item.order + 1}. {item.milestone_title}</h2>
          </div>
          <div className="amount">{money(item.amount, item.currency)}</div>
        </div>
        <p className="expected"><strong>Photo should show:</strong> {item.proof_description}</p>
        <p className="muted small-text">
          Worker {item.worker_email}
          {sub && <> · submitted {formatTime(sub.created_at)} · attempt {item.attempts}</>}
        </p>

        {sub && (
          <>
            <div className="score-line">
              <span>AI confidence</span>
              <Meter value={sub.confidence} />
              <strong>{Math.round(sub.confidence * 100)}%</strong>
              <Status value={sub.decision} />
            </div>
            <ul className="checks">
              {Object.entries(sub.checks || {}).map(([name, c]) => {
                const passed = c.score >= 0.7 && !c.blocks_auto && c.connected !== false
                return (
                <li key={name} className={c.hard_flag ? 'flag' : passed ? '' : 'warn'}>
                  <span className="check-icon" aria-hidden>{c.hard_flag ? '✕' : passed ? '✓' : '!'}</span>
                  <span className="check-name">{CHECK_LABELS[name] || name}</span>
                  <span className="check-reason">{c.reason}</span>
                </li>
                )
              })}
            </ul>
            {sub.reasons?.filter(r => r.startsWith('gate:')).map(r => (
              <p key={r} className="gate">{r.replace('gate: ', 'Sent to you: ')}</p>
            ))}
          </>
        )}

        {item.needs === 'payout_problem' ? (
          <div className="actions">
            <p className="alert bad">
              <PayoutStatus value={item.payout_status} /> Paid to {item.worker_email}, but not received.{' '}
              {payoutHelp(item.payout_status)}
            </p>
            <button className="ghost" disabled={busy} onClick={() => act('check')}>
              {busy === 'check' ? 'Checking…' : 'Check with PayPal again'}
            </button>
          </div>
        ) : item.needs === 'retry' ? (
          <div className="actions">
            <p className="alert warn">
              {item.status === 'CAPTURED'
                ? 'Funds were captured but the payout to the worker failed.'
                : 'Approved, but the payment did not go through.'} Retrying is safe: money never moves twice.
            </p>
            <button className="primary" disabled={busy} onClick={() => act('retry')}>
              {busy === 'retry' ? 'Retrying…' : 'Retry payout'}
            </button>
          </div>
        ) : (
          <div className="actions">
            <input placeholder="Note (optional)" value={note} onChange={e => setNote(e.target.value)} />
            <button className="danger-ghost" disabled={busy} onClick={() => act('reject')}>
              {busy === 'reject' ? 'Rejecting…' : 'Reject'}
            </button>
            <button className="primary" disabled={busy} onClick={() => act('approve')}>
              {busy === 'approve' ? 'Paying…' : `Approve & pay ${money(item.amount, item.currency)}`}
            </button>
          </div>
        )}
        <ErrorBox error={error} />
      </div>
    </article>
  )
}

function Meter({ value }) {
  const tone = value >= 0.85 ? 'good' : value >= 0.5 ? 'warn' : 'bad'
  return (
    <span className="meter" role="meter" aria-valuemin={0} aria-valuemax={1} aria-valuenow={value}>
      <span className={`meter-fill ${tone}`} style={{ width: `${Math.round(value * 100)}%` }} />
    </span>
  )
}
