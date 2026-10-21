// Thin client for the FastAPI backend. Admin token lives in localStorage.
// Dev: the API runs separately on :8000. Production build: same origin, under /api (see backend/app/server.py).
export const API_URL = import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? 'http://localhost:8000' : '/api')

const TOKEN_KEY = 'pop_admin_token'

export function getToken() {
  try { return localStorage.getItem(TOKEN_KEY) } catch { return null }
}

export function setToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch { /* private mode: stay logged in for this tab only */ }
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

/**
 * api('/projects')                              GET as admin
 * api('/projects', { method: 'POST', body })    JSON body
 * api('/milestones/1/proof', { form, auth: false })  multipart, no admin token
 */
export async function api(path, { method, body, form, auth = true } = {}) {
  const headers = {}
  if (auth && getToken()) headers.Authorization = `Bearer ${getToken()}`
  let payload
  if (form) payload = form
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  method = method || (payload !== undefined ? 'POST' : 'GET')

  let res
  try {
    res = await fetch(`${API_URL}${path}`, { method, headers, body: payload })
  } catch {
    throw new ApiError(0, `Cannot reach the server at ${API_URL}. Is the backend running?`)
  }

  if (res.status === 401 && auth) {
    setToken(null)
    window.dispatchEvent(new Event('pop:logout'))
  }
  const data = res.headers.get('content-type')?.includes('json') ? await res.json() : await res.text()
  if (!res.ok) {
    const detail = typeof data === 'object' ? data.detail : data
    throw new ApiError(res.status, typeof detail === 'string' ? detail : `Request failed (${res.status})`)
  }
  return data
}

/** Fetches an admin-only image and returns an object URL for <img src>. */
export async function fetchImageUrl(path) {
  const res = await fetch(`${API_URL}${path}`, { headers: { Authorization: `Bearer ${getToken()}` } })
  if (!res.ok) throw new ApiError(res.status, 'Image not available')
  return URL.createObjectURL(await res.blob())
}

/** Tell the shell (review-queue badge) that data changed. */
export function notifyChanged() {
  window.dispatchEvent(new Event('pop:changed'))
}

export function money(amount, currency = 'USD') {
  return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(amount ?? 0)
}

export function workerLink(project) {
  return `${window.location.origin}/w/${project.id}?t=${project.worker_token}`
}
