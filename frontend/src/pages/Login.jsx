import { useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { api, getToken, setToken } from '../api.js'
import { ErrorBox } from '../components.jsx'

export default function Login() {
  const navigate = useNavigate()
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  if (getToken()) return <Navigate to="/" replace />

  async function submit(e) {
    e.preventDefault()
    setBusy(true); setError(null)
    try {
      const { token } = await api('/auth/login', { body: { password }, auth: false })
      setToken(token)
      navigate('/')
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      <form className="card login-card" onSubmit={submit}>
        <div className="brand"><span className="brand-mark">◆</span> Pay-on-Proof</div>
        <p className="muted">Admin dashboard. Approve verified work and release escrowed funds.</p>
        <label>
          Admin password
          <input type="password" autoFocus value={password} onChange={e => setPassword(e.target.value)} required />
        </label>
        <ErrorBox error={error} />
        <button className="primary" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
      </form>
    </div>
  )
}
