// Supabase Auth REST; public project key only. Session lasts for this browser tab.
const KEY = 'fish-auth-session';
const AUTH_URL = (import.meta.env.VITE_SUPABASE_URL ?? '').replace(/\/$/, '');
const PUBLIC_KEY = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY ?? '';
let memory;
let epoch = 0;
let refreshing = null;
const emit = (name) => { if (typeof window !== 'undefined') window.dispatchEvent(new Event(name)); };
export const sessionEpoch = () => epoch;
export function session() {
  if (memory !== undefined) return memory;
  try { memory = JSON.parse(sessionStorage.getItem(KEY)) ?? null; } catch { memory = null; }
  return memory;
}
function save(value, changed = false) {
  memory = value;
  if (changed) epoch += 1;
  try { if (value) sessionStorage.setItem(KEY, JSON.stringify(value)); else sessionStorage.removeItem(KEY); } catch { /* tab memory still works */ }
  emit('auth-session-changed');
}
export function clearSession() { save(null, true); }
function safeError(status, code) { return Object.assign(new Error('인증을 확인해 주세요.'), { status, code }); }
async function auth(path, body, token) {
  let url;
  try { url = new URL(AUTH_URL); } catch { throw safeError(503, 'auth_not_configured'); }
  if ((url.protocol !== 'https:' && !(url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)))
      || url.username || url.password || url.search || url.hash || url.pathname !== '/' || !PUBLIC_KEY || PUBLIC_KEY.startsWith('sb_secret_')) {
    throw safeError(503, 'auth_not_configured');
  }
  const response = await fetch(`${AUTH_URL}/auth/v1/${path}`, { method: 'POST',
    headers: { apikey: PUBLIC_KEY, 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}) });
  if (!response.ok) throw safeError(response.status, 'auth_failed');
  return response.status === 204 ? null : response.json();
}
function valid(value) { return value && typeof value.access_token === 'string' && typeof value.refresh_token === 'string' && value.access_token && value.refresh_token; }
export async function signIn(email, password) {
  const value = await auth('token?grant_type=password', { email, password });
  if (!valid(value)) throw safeError(503, 'invalid_auth_response');
  save(value, true);
}
export async function refreshSession(usedToken) {
  if (session()?.access_token && session().access_token !== usedToken) return;
  if (refreshing) return refreshing;
  const before = epoch;
  const refreshToken = session()?.refresh_token;
  if (!refreshToken) { clearSession(); throw safeError(401, 'session_expired'); }
  refreshing = (async () => {
    try {
      const value = await auth('token?grant_type=refresh_token', { refresh_token: refreshToken });
      if (before !== epoch) throw safeError(401, 'session_changed');
      if (!valid(value)) throw safeError(503, 'invalid_auth_response');
      save(value);
    } catch (e) { if (before === epoch) clearSession(); throw e; }
    finally { refreshing = null; }
  })();
  return refreshing;
}
export async function signOut() {
  const token = session()?.access_token;
  clearSession();
  if (token) await auth('logout?scope=local', null, token).catch(() => {});
}
export function forbidden() { emit('protected-data-forbidden'); }
