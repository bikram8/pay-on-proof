import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, money } from '../api.js'
import { ErrorBox } from '../components.jsx'

const blankMilestone = () => ({ title: '', proof_description: '', amount: '', worker_email: '' })

export default function NewProject() {
  const navigate = useNavigate()
  const [form, setForm] = useState({
    title: '', description: '', escrow_mode: 'AUTH_CAPTURE',
    site_lat: '', site_lon: '', site_radius_m: 500,
  })
  const [milestones, setMilestones] = useState([blankMilestone()])
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [locating, setLocating] = useState(false)

  const set = key => e => setForm({ ...form, [key]: e.target.value })
  const setM = (i, key) => e => setMilestones(milestones.map((m, j) => j === i ? { ...m, [key]: e.target.value } : m))
  const total = milestones.reduce((s, m) => s + (parseFloat(m.amount) || 0), 0)

  function useMyLocation() {
    if (!navigator.geolocation) return setError(new Error('This browser cannot share location.'))
    setLocating(true)
    navigator.geolocation.getCurrentPosition(
      pos => {
        setForm(f => ({ ...f, site_lat: pos.coords.latitude.toFixed(6), site_lon: pos.coords.longitude.toFixed(6) }))
        setLocating(false)
      },
      err => { setError(new Error(`Location unavailable: ${err.message}`)); setLocating(false) },
      { enableHighAccuracy: true, timeout: 15000 },
    )
  }

  async function submit(e) {
    e.preventDefault()
    setBusy(true); setError(null)
    try {
      const num = v => (v === '' ? null : Number(v))
      const p = await api('/projects', {
        body: {
          ...form,
          site_lat: num(form.site_lat), site_lon: num(form.site_lon), site_radius_m: Number(form.site_radius_m),
          milestones: milestones.map(m => ({ ...m, amount: Number(m.amount) })),
        },
      })
      navigate(`/projects/${p.id}`)
    } catch (err) {
      setError(err)
      setBusy(false)
    }
  }

  return (
    <section className="narrow">
      <div className="page-head"><h1>New project</h1></div>
      <form onSubmit={submit} className="stack">
        <div className="card stack">
          <h2>Project</h2>
          <label>Title<input value={form.title} onChange={set('title')} required placeholder="Mangrove replanting, Kendrapara" /></label>
          <label>Description<textarea value={form.description} onChange={set('description')} rows={2} /></label>
          <label>
            Escrow mode
            <select value={form.escrow_mode} onChange={set('escrow_mode')}>
              <option value="AUTH_CAPTURE">Hold & capture: short projects (finish within ~29 days)</option>
              <option value="ESCROW_PAYOUT">Escrow & payout: long milestones (e.g. 60-day survival check)</option>
            </select>
          </label>
        </div>

        <div className="card stack">
          <div className="row-between">
            <h2>Work site</h2>
            <button type="button" className="ghost small" onClick={useMyLocation} disabled={locating}>
              {locating ? 'Locating…' : 'Use my current location'}
            </button>
          </div>
          <p className="muted small-text">Proof photos must be taken within this radius. Leave empty to skip the location check.</p>
          <div className="cols-3">
            <label>Latitude<input type="number" step="any" value={form.site_lat} onChange={set('site_lat')} /></label>
            <label>Longitude<input type="number" step="any" value={form.site_lon} onChange={set('site_lon')} /></label>
            <label>Radius (m)<input type="number" min="10" value={form.site_radius_m} onChange={set('site_radius_m')} /></label>
          </div>
        </div>

        <div className="card stack">
          <h2>Milestones</h2>
          <p className="muted small-text">Paid in order. Each one is released only when its proof photo is verified.</p>
          {milestones.map((m, i) => (
            <fieldset key={i} className="milestone-fields">
              <legend>Milestone {i + 1}</legend>
              <div className="cols-2">
                <label>Title<input value={m.title} onChange={setM(i, 'title')} required placeholder="Plant saplings" /></label>
                <label>Amount<input type="number" min="0.01" step="0.01" value={m.amount} onChange={setM(i, 'amount')} required /></label>
              </div>
              <label>Photo must show<input value={m.proof_description} onChange={setM(i, 'proof_description')} required placeholder="Saplings planted in mud along the shore" /></label>
              <label>Worker's PayPal email<input type="email" value={m.worker_email} onChange={setM(i, 'worker_email')} required /></label>
              {milestones.length > 1 && (
                <button type="button" className="danger-ghost small" onClick={() => setMilestones(milestones.filter((_, j) => j !== i))}>Remove</button>
              )}
            </fieldset>
          ))}
          <button type="button" className="ghost" onClick={() => setMilestones([...milestones, { ...blankMilestone(), worker_email: milestones.at(-1).worker_email }])}>
            + Add milestone
          </button>
        </div>

        <ErrorBox error={error} />
        <div className="row-between sticky-foot">
          <div>Total to escrow <strong>{money(total)}</strong></div>
          <button className="primary" disabled={busy}>{busy ? 'Creating…' : 'Create project'}</button>
        </div>
      </form>
    </section>
  )
}
