import type { ApiErrorBody } from './types'

/**
 * Origin for API calls. VITE_API_BASE_URL is the backend origin (for example
 * http://127.0.0.1:8000). Unset means same-origin, which Vite proxies to the
 * backend in `npm run dev`.
 */
export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')

export function apiUrl(path: string): string {
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`
}