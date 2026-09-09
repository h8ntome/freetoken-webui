let csrf = ''

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) { super(message); this.status = status }
}

export function setCsrf(value: string) { csrf = value }

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  if (options.method && options.method !== 'GET' && csrf) headers.set('X-CSRF-Token', csrf)
  const response = await fetch(path, {...options, headers, credentials: 'same-origin'})
  const text = await response.text()
  let value: any = null
  try { value = text ? JSON.parse(text) : null } catch { value = {detail: text} }
  if (!response.ok) throw new ApiError((typeof value?.detail === "string" ? value.detail : value?.detail ? JSON.stringify(value.detail) : typeof value?.error === "string" ? value.error : value?.error ? JSON.stringify(value.error) : `Request failed (${response.status})`), response.status)
  return value as T
}

export const post = <T,>(path: string, body?: unknown) => api<T>(path, {method: 'POST', body: body === undefined ? undefined : JSON.stringify(body)})
export const patch = <T,>(path: string, body: unknown) => api<T>(path, {method: 'PATCH', body: JSON.stringify(body)})
export const del = <T,>(path: string) => api<T>(path, {method: 'DELETE'})

export function formatBytes(value?: number | null) {
  if (value == null) return '—'
  if (value === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1)
  return `${(value / 1024 ** i).toFixed(i > 2 ? 1 : 0)} ${units[i]}`
}

export function formatTime(value?: number | null) {
  if (!value) return '—'
  return new Intl.DateTimeFormat(undefined, {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'}).format(new Date(value * 1000))
}

