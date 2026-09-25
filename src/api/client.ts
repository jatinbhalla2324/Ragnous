import axios from 'axios';

/* One place to say where the backend lives. Note the deliberate absence of any
   key here: secrets go in the backend's .env, because everything a VITE_ var
   touches is compiled into the bundle and served to every visitor. */
export const API_BASE_URL =
  import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

/** ws:// or wss:// twin of API_BASE_URL, for the Deepgram relay endpoints. */
export const WS_BASE_URL = API_BASE_URL.replace(/^http/, 'ws');

// Create a configured axios client pointing to the FastAPI backend
export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});
