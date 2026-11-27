import { useLocation, useNavigate } from 'react-router-dom';
import { useState } from 'react';
import { DEMO_ACCOUNTS, useAuth } from '../auth/AuthContext.jsx';
import { usePageTitle } from '../usePageTitle.js';

// 시연용 로그인: 비밀번호 없이 계정을 골라 들어간다.
export default function LoginPage() {
  usePageTitle('로그인');
  const { user, login, logout, loginReal, loading } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const navigate = useNavigate();
  const { state } = useLocation();

  const enter = (role) => {
    login(role);
    const next = state?.next;
    // 가려던 화면이 이 계정의 화면일 때만 거기로 간다.
    const allowed = role === 'farmer' ? ['/farmer', '/stock', '/notifications'] : ['/dashboard'];
    navigate(allowed.includes(next) ? next : DEMO_ACCOUNTS[role].home, { replace: true });
  };

  return (
    <div className="login">
      <h1>로그인</h1>
      {state?.reason && <p className="notice" role="status">{state.reason}</p>}
      <p className="lead">시연용 계정입니다. 비밀번호 없이 들어갈 계정을 골라 주세요.</p>
      <ul className="accounts">
        {Object.values(DEMO_ACCOUNTS).map((a) => (
          <li key={a.role}>
            <div>
              <strong>{a.roleLabel}</strong>
              <span>{a.name} · {a.detail}</span>
            </div>
            <button type="button" className="btn btn-dark" onClick={() => enter(a.role)}>
              {user?.role === a.role ? '이 계정으로 계속' : `${a.roleLabel}로 들어가기`}
            </button>
          </li>
        ))}
      </ul>
      <details className="real-login-box">
        <summary>실제 계정으로 로그인</summary>
        <form className="real-login" onSubmit={async (e) => {
          e.preventDefault(); setError('');
          try {
            const p = await loginReal(email, password); setPassword('');
            navigate(p.role === 'official' ? '/dashboard' : p.role === 'farmer' ? '/farmer' : '/', { replace: true });
          } catch { setError('로그인하지 못했습니다. 계정과 서비스 설정을 확인해 주세요.'); }
        }}>
          <label>이메일 <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
          <label>비밀번호 <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
          <button className="primary" disabled={loading}>로그인</button>
          {error && <p className="error" role="alert">{error}</p>}
        </form>
      </details>
      {user && (
        <p className="hint">
          지금 {user.name}({user.roleLabel})으로 들어와 있습니다.{' '}
          <button type="button" className="link-btn" onClick={logout}>로그아웃</button>
        </p>
      )}
    </div>
  );
}
