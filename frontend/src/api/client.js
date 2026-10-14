const API_URL = import.meta.env.VITE_API_URL

export class ApiError extends Error {
  constructor(message, status, retryAfter) {
    super(message)
    this.status = status
    this.retryAfter = retryAfter
  }
}

export async function apiGet(path, params = {}, { signal } = {}) {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''),
  )
  const res = await fetch(`${API_URL}${path}?${query}`, { signal })
  let body = null
  try {
    body = await res.json()
  } catch {
    // non-JSON error page (e.g. proxy); fall back to the status code below
  }
  if (!res.ok) {
    throw new ApiError(errorMessage(res.status, body), res.status, res.headers.get('Retry-After'))
  }
  return body
}

function errorMessage(status, body) {
  if (status === 429) return `Too many requests. ${body?.detail ?? ''}`.trim()
  if (body?.detail) return body.detail
  if (body && typeof body === 'object') return Object.values(body).flat().join(' ')
  return `Request failed (${status}).`
}
