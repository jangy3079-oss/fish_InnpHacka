import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { api } from '../api/client.js';
import { session, sessionEpoch, signIn, signOut, clearSession } from './session.js';

// 시연용 로그인. 서버·토큰 없이 이 브라우저(localStorage)에만 저장한다. 실제 인증이 아니다.
export const DEMO_ACCOUNTS = {
  farmer: { role: 'farmer', name: '김바다', roleLabel: '어민', detail: '가두리 3번 · 조피볼락', home: '/farmer' },
  official: { role: 'official', name: '박해양', roleLabel: '지자체 담당자', detail: '시연 해역 3곳 담당', home: '/dashboard' },
};

const KEY = 'demo-user';
const AuthContext = createContext(null);

function load() {
  try {
    const role = JSON.parse(localStorage.getItem(KEY))?.role;
    return DEMO_ACCOUNTS[role] ?? null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(load);
  const [loading, setLoading] = useState(!!session());
  const [blocked, setBlocked] = useState(false);
  const [cageId, setCageId] = useState(3);
  const [version, setVersion] = useState(0);
  const profile = async () => {
    const started = sessionEpoch();
    const p = await api.getMe();
    if (started !== sessionEpoch() || !session()) throw new Error('Session changed');
    const roleLabel = { official: '지자체 담당자', farmer: '어민', admin: '관리자', edge: '수집 계정', analysis: '분석 계정', unassigned: '역할 미지정' }[p.role];
    setUser({ ...p, real: true, name: roleLabel, roleLabel, home: p.role === 'official' ? '/dashboard' : '/farmer' });
    const selectable = p.cages.some((c) => c.can_write_stock) ? p.cages.filter((c) => c.can_write_stock) : p.cages;
    setCageId((old) => selectable.some((c) => c.id === old) ? old : selectable[0]?.id ?? null);
    setBlocked(false); setVersion((v) => v + 1);
    return p;
  };
  useEffect(() => {
    if (session()) profile().catch(() => { clearSession(); setUser(null); }).finally(() => setLoading(false));
    const changed = () => { if (!session()) { setUser((old) => old?.real ? null : old); setBlocked(false); } };
    const denied = () => setBlocked(true);
    window.addEventListener('auth-session-changed', changed);
    window.addEventListener('protected-data-forbidden', denied);
    return () => {
      window.removeEventListener('auth-session-changed', changed);
      window.removeEventListener('protected-data-forbidden', denied);
    };
  }, []);
  const value = useMemo(
    () => ({
      user,
      loading, blocked, cageId, version,
      selectCage: (id) => {
        if (user?.cages?.some((c) => c.id === id)) setCageId(id);
      },
      reloadAccess: async () => { setLoading(true); try { await profile(); } finally { setLoading(false); } },
      loginReal: async (email, password) => {
        setLoading(true); setUser(null); localStorage.removeItem(KEY); clearSession();
        try { await signIn(email, password); return await profile(); }
        catch (e) { clearSession(); throw e; }
        finally { setLoading(false); }
      },
      login: (role) => {
        clearSession(); setBlocked(false); setCageId(3);
        localStorage.setItem(KEY, JSON.stringify({ role }));
        setUser(DEMO_ACCOUNTS[role]);
      },
      logout: () => {
        signOut(); setBlocked(false); setCageId(null);
        localStorage.removeItem(KEY);
        setUser(null);
      },
    }),
    [user, loading, blocked, cageId, version],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);

// 역할이 맞지 않으면 로그인 화면으로 보내고, 로그인한 뒤 원래 가려던 곳으로 돌아오게 한다.
export function RequireRole({ role, children }) {
  const { user, loading, blocked, reloadAccess, version } = useAuth();
  const location = useLocation();
  if (loading) return <p role="status">접근 권한을 확인하고 있습니다.</p>;
  if (blocked) return <section className="card"><p>접근 권한이 변경되어 이전 데이터를 지웠습니다.</p><button onClick={() => reloadAccess().catch(() => {})}>권한 다시 확인</button></section>;
  if (user?.role === role || (role === 'farmer' && user?.cages?.some((c) => c.can_write_stock))) return <div key={version}>{children}</div>;
  const reason = user ? `${DEMO_ACCOUNTS[role].roleLabel} 계정으로 들어가야 볼 수 있는 화면입니다.` : null;
  return <Navigate to="/login" replace state={{ next: location.pathname, reason }} />;
}
