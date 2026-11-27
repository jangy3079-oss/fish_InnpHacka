import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom';
import { SITE_NAME } from '../constants.js';
import { useAuth } from '../auth/AuthContext.jsx';

const FARMER_PATHS = ['/farmer', '/stock', '/notifications'];

function Logo() {
  return (
    <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
      <rect width="28" height="28" rx="7" fill="#0b2230" />
      <path d="M4 11c3-3 5-3 8 0s5 3 8 0M4 17c3-3 5-3 8 0s5 3 8 0" fill="none" stroke="#9fc4d4" strokeWidth="1.8" strokeLinecap="round" />
      <ellipse cx="20.5" cy="21" rx="3.2" ry="1.7" fill="#f2c14e" />
    </svg>
  );
}

export function SiteHeader() {
  const { pathname } = useLocation();
  const farmerActive = FARMER_PATHS.includes(pathname);
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  return (
    <header className="site-header">
      <div className="site-header-inner">
        <Link to="/" className="wordmark">
          <Logo />
          <span>{SITE_NAME}</span>
        </Link>
        <nav aria-label="주 메뉴">
          <NavLink to="/" end>서비스 소개</NavLink>
          {(user?.role === 'farmer' || user?.cages?.some((c) => c.can_write_stock)) && <NavLink to="/farmer" className={farmerActive ? 'active' : undefined}>어민 화면</NavLink>}
          {user?.role === 'official' && <NavLink to="/dashboard">지자체 대시보드</NavLink>}
        </nav>
        <div className="account">
          {user ? (
            <>
              <span><strong>{user.name}</strong> {user.roleLabel}</span>
              <button type="button" onClick={() => { logout(); navigate('/'); }}>로그아웃</button>
            </>
          ) : (
            <Link to="/login">로그인</Link>
          )}
        </div>
      </div>
    </header>
  );
}

export function SiteFooter() {
  const { user } = useAuth();
  return (
    <footer className="site-footer">
      <div className="site-footer-inner">
        <p className="footer-title">{SITE_NAME}</p>
        <p>{user?.real ? '실제 계정의 접근 가능한 관측 정보를 조회합니다. 파일 시연은 별도로 표시합니다.' : '해커톤 시연용 화면입니다. 화면의 모든 수치는 시뮬레이션 데이터입니다.'}</p>
        <p>의심 원인은 추정이며 확정 진단이 아닙니다. 질병이 의심되면 수산질병관리사에게 진단을 요청해 주세요.</p>
        <nav aria-label="바로가기">
          <Link to="/">서비스 소개</Link>
          <Link to="/farmer">어민 화면</Link>
          <Link to="/dashboard">지자체 대시보드</Link>
        </nav>
      </div>
    </footer>
  );
}

// 어민 화면 안에서 이동하는 탭
export function FarmerTabs() {
  const { user, cageId, selectCage } = useAuth();
  return (
    <nav className="tabs" aria-label="어민 화면 메뉴">
      {user?.real && <label>가두리 <select value={cageId ?? ''} onChange={(e) => selectCage(Number(e.target.value))}>
        {user.cages.filter((c) => c.can_write_stock).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
      </select></label>}
      <NavLink to="/farmer">현황</NavLink>
      <NavLink to="/stock">재고 입력</NavLink>
      <NavLink to="/notifications">알림</NavLink>
    </nav>
  );
}
