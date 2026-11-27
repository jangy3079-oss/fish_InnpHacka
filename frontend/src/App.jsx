import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import FarmerPage from './pages/FarmerPage.jsx';
import DashboardPage from './pages/DashboardPage.jsx';
import StockPage from './pages/StockPage.jsx';
import NotificationsPage from './pages/NotificationsPage.jsx';
import HomePage from './pages/HomePage.jsx';
import DemoBar from './components/DemoBar.jsx';
import { SiteFooter, SiteHeader } from './components/SiteChrome.jsx';
import { DemoProvider, useDemo } from './demo/DemoContext.jsx';
import LevelToast from './components/LevelToast.jsx';
import { api } from './api/client.js';
import LoginPage from './pages/LoginPage.jsx';
import { AuthProvider, RequireRole, useAuth } from './auth/AuthContext.jsx';

// 어민 현황·대시보드·알림은 시연 데이터를 보여 주므로 실제 값으로 오해하지 않게 알린다.
// 어민 현황은 실제 모드에서도 시연 응답(합성)을 쓴다. 대시보드·알림은 실제 모드에서 실제 값이므로 목업일 때만 표시한다.
function SimBanner({ pathname }) {
  const { demo } = useDemo();
  const fileData = api.files(demo); // 파일 재생이면 대시보드·알림도 합성 데이터다
  const show = pathname === '/farmer' || (fileData && (pathname === '/dashboard' || pathname === '/notifications'));
  if (!show) return null;
  return (
    <p className="sim-banner" role="note">
      합성 시연 데이터입니다. 실제 가두리 값이 아닙니다.{!api.isMock && fileData && ' (파일 재생 중)'}
    </p>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppBody />
    </AuthProvider>
  );
}

function AppBody() {
  const { pathname } = useLocation();
  const { user } = useAuth();
  const isHome = pathname === '/';
  return (
    <DemoProvider>
      <a className="skip-link" href="#main">본문 바로가기</a>
      <SiteHeader />
      <SimBanner pathname={pathname} />
      <main id="main" tabIndex={-1} className={isHome ? 'home' : 'container'}>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/farmer" element={<RequireRole role="farmer"><FarmerPage /></RequireRole>} />
          <Route path="/stock" element={<RequireRole role="farmer"><StockPage /></RequireRole>} />
          <Route path="/notifications" element={<RequireRole role="farmer"><NotificationsPage /></RequireRole>} />
          <Route path="/dashboard" element={<RequireRole role="official"><DashboardPage /></RequireRole>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <SiteFooter />
      {!isHome && pathname !== '/login' && user && <DemoBar />}
      {user?.role === 'farmer' && <LevelToast cageId={3} />}
    </DemoProvider>
  );
}
