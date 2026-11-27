import { createContext, useContext, useMemo, useState } from 'react';
import { Navigate, useLocation } from 'react-router-dom';

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
  const value = useMemo(
    () => ({
      user,
      login: (role) => {
        localStorage.setItem(KEY, JSON.stringify({ role }));
        setUser(DEMO_ACCOUNTS[role]);
      },
      logout: () => {
        localStorage.removeItem(KEY);
        setUser(null);
      },
    }),
    [user],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);

// 역할이 맞지 않으면 로그인 화면으로 보내고, 로그인한 뒤 원래 가려던 곳으로 돌아오게 한다.
export function RequireRole({ role, children }) {
  const { user } = useAuth();
  const location = useLocation();
  if (user?.role === role) return children;
  const reason = user ? `${DEMO_ACCOUNTS[role].roleLabel} 계정으로 들어가야 볼 수 있는 화면입니다.` : null;
  return <Navigate to="/login" replace state={{ next: location.pathname, reason }} />;
}
