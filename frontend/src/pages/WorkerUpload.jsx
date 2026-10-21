import { useCallback, useEffect, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { api, money } from '../api.js'
import { ErrorBox, Status } from '../components.jsx'

/** Mobile page for field workers, opened from the project's signed link. No login. */
export default function WorkerUpload() {
  const { projectId } = useParams()
  const [params] = useSearchParams()
  const t = params.get('t') || ''
  const [project, setProject] = useState(null)
  const [error, setError] = useState(null)
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState(null)
  const [location, setLocation] = useState({ state: 'idle' })
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)

  const load = useCallback(() => {
    api(`/worker/projects/${projectId}?t=${encodeURIComponent(t)}`, { auth: false })
      .then(setProject).catch(setError)
  }, [projectId, t])
  useEffect(() => { load() }, [load])

  // Ask for location up front: many phones strip GPS from photos picked in the browser.
  useEffect(() => {
    if (!navigator.geolocation) return setLocation({ state: 'unsupported' })
    setLocation({ state: 'asking' })
    const id = navigator.geolocation.watchPosition(
      pos => setLocation({ state: 'ok', lat: pos.coords.latitude, lon: pos.coords.longitude, accuracy: pos.coords.accuracy }),
      err => setLocation({ state: 'denied', message: err.message }),
      { enableHighAccuracy: true, maximumAge: 30000, timeout: 20000 },
    )
    return () => navigator.geolocation.clearWatch(id)
  }, [])

  useEffect(() => {
    if (!file) return setPreview(null)
    const url = URL.createObjectURL(file)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [file])

  const current = project?.milestones.find(m => m.id === project.current_milestone_id)
  const canSubmit = current && ['PENDING', 'REJECTED'].includes(current.status) && project.accepting_proof

  async function submit() {
    setBusy(true); setError(null); setResult(null)
    const form = new FormData()
    form.append('file', file)
    form.append('t', t)
    if (location.state === 'ok') {
      form.append('device_lat', location.lat)
      form.append('device_lon', location.lon)
      form.append('device_accuracy_m', location.accuracy)
    }
    try {
      const r = await api(`/milestones/${current.id}/proof`, { form, auth: false })
      setResult(r)
      setFile(null)
      load()
    } catch (err) { setError(err) } finally { setBusy(false) }
  }

  if (!project) {
    return (
      <div className="worker">
        {error ? <ErrorBox error={error.status === 403 ? new Error('This upload link is not valid. Ask for a new one.') : error} />
          : <p className="muted">Loading…</p>}
      </div>
    )
  }

  return (
    <div className="worker">
      <header>
        <div className="brand small"><span className="brand-mark">◆</span> Pay-on-Proof</div>
        <h1>{project.title}</h1>
      </header>

      {result && <div className={`alert ${result.paid ? 'good' : result.milestone_status === 'REJECTED' ? 'bad' : 'info'}`}>{result.message}</div>}

      {!project.accepting_proof && project.status !== 'COMPLETED' && (
        <div className="alert info">This project isn't funded yet, so proof can't be submitted. Check back later.</div>
      )}
      {project.status === 'COMPLETED' && <div className="alert good">All milestones are complete and paid. Thank you!</div>}

      {current && (
        <div className="card stack">
          <div className="row-between">
            <span className="muted small-text">Milestone {current.order + 1} of {project.milestones.length}</span>
            <Status value={current.status} />
          </div>
          <h2>{current.title}</h2>
          <p className="expected"><strong>Take a photo showing:</strong> {current.proof_description}</p>
          <p className="muted small-text">Payment on approval: {money(current.amount, project.currency)}</p>

          {canSubmit && (
            <>
              <LocationNote location={location} />
              <label className="camera-button">
                <input type="file" accept="image/*" capture="environment"
                  onChange={e => { setFile(e.target.files?.[0] || null); setResult(null) }} />
                {file ? 'Retake photo' : 'Take photo'}
              </label>
              {preview && <img className="preview" src={preview} alt="Your photo" />}
              <ErrorBox error={error} />
              <button className="primary big" disabled={!file || busy} onClick={submit}>
                {busy ? 'Checking your photo…' : 'Submit proof'}
              </button>
              {busy && <p className="muted small-text">The AI is reviewing your photo. This usually takes 10–30 seconds; please keep this page open.</p>}
              <p className="muted small-text">Take the photo now, at the site. Old photos or photos from the internet are rejected.</p>
            </>
          )}
          {current.status === 'HUMAN_REVIEW' && <p className="muted">A reviewer is checking your photo. You'll be paid once it's approved.</p>}
        </div>
      )}

      <ol className="worker-steps">
        {project.milestones.map(m => (
          <li key={m.id} className={m.id === project.current_milestone_id ? 'current' : ''}>
            <span>{m.title}</span>
            <Status value={m.status} />
          </li>
        ))}
      </ol>
    </div>
  )
}

function LocationNote({ location }) {
  if (location.state === 'ok') {
    return <p className="small-text good-text">📍 Location on (±{Math.round(location.accuracy)} m)</p>
  }
  if (location.state === 'asking') return <p className="small-text muted">📍 Getting your location…</p>
  return (
    <p className="small-text warn-text">
      📍 Location is off. Allow location for this page so we can confirm you're at the site.
    </p>
  )
}
