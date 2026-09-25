/**
 * Where the FastAPI backend lives.
 *
 * The URL used to be typed out in each API module, which meant pointing the app
 * at a second backend (a staging box, or a local instance on another port while
 * one is already running) required editing every call site. Set VITE_API_URL to
 * override; the default is the local dev server.
 */
export const API_BASE =
  (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') ??
  'http://127.0.0.1:8000/api/v1';
