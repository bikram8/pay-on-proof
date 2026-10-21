import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { api, getToken, setToken } from './api.js'
import Login from './pages/Login.jsx'
import ReviewQueue from './pages/ReviewQueue.jsx'
import Projects from './pages/Projects.jsx'
import NewProject from './pages/NewProject.jsx'
import ProjectDetail from './pages/ProjectDetail.jsx'
import Activity from './pages/Activity.jsx'
import WorkerUpload from './pages/WorkerUpload.jsx'

export default function App() {
  return (
    <Routes>
      {/* Worker page: no login, no admin navigation */}
      <Route path="/w/:projectId" element={<WorkerUpload />} />
      <Route path="/login" element={<Login />} />
      <Route path="/*" element={<RequireAdmin><AdminShell /></RequireAdmin>} />
    </Routes>
  )
}

function RequireAdmin({ children }) {
  const [loggedIn, setLoggedIn] = useState(Boolean(getToken()))
  useEffect(() => {
    const out = () => setLoggedIn(false)
    window.addEventListener('pop:logout', out)
    return () => window.removeEventListener('pop:logout', out)
  }, [])
  return loggedIn ? children : <Navigate to="/login" replace />
}

function AdminShell() {
  const navigate = useNavigate()
  const [queueCount, setQueueCount] = useState(null)

  useEffect(() => {
    let alive = true
    const load = () => api('/review-queue').then(q => alive && setQueueCount(q.length)).catch(() => {})
    load()
    const id = setInterval(load, 10000)
    window.addEventListener('pop:changed', load)
    return () => { alive = false; clearInterval(id); window.removeEventListener('pop:changed', load) }
  }, [])

  function logout() {
    setToken(null)
    navigate('/login')
  }

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand"><span className="brand-mark">◆</span> Pay-on-Proof</div>
        <nav>
          <NavLink to="/" end>
            Review queue {queueCount > 0 && <span className="count">{queueCount}</span>}
          </NavLink>
          <NavLink to="/projects">Projects</NavLink>
          <NavLink to="/activity">Activity</NavLink>
        </nav>
        <button className="ghost small" onClick={logout}>Log out</button>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<ReviewQueue />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/projects/new" element={<NewProject />} />
          <Route path="/projects/:id" element={<ProjectDetail />} />
          <Route path="/activity" element={<Activity />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  )
}
