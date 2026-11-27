import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import FarmerPage from './pages/FarmerPage.jsx';
import DashboardPage from './pages/DashboardPage.jsx';
import StockPage from './pages/StockPage.jsx';
import NotificationsPage from './pages/NotificationsPage.jsx';
import HomePage from './pages/HomePage.jsx';
import DemoBar from './components/DemoBar.jsx';
import { SiteFooter, SiteHeader } from './components/SiteChrome.jsx';
import { DemoProvider } from './demo/DemoContext.jsx';
import LevelToast from './components/LevelToast.jsx';
import { api } from './api/client.js';

// 어민 현황·대시보드·알림은 시연 데이터를 보여 주므로 실제 값으로 오해하지 않게 알린다.
// 어민 현황은 실제 모드에서도 시연 응답(합성)을 쓴다. 대시보드·알림은 실제 모드에서 실제 값이므로 목업일 때만 표시한다.
const isSimulated = (pathname) =>
  pathname === '/farmer' || (api.isMock && (pathname === '/dashboard' || pathname === '/notifications'));

export default function App() {
  const { pathname } = useLocation();
  const isHome = pathname === '/';
  return (
    <DemoProvider>
      <a className="skip-link" href="#main">본문 바로가기</a>
      <SiteHeader />
      {isSimulated(pathname) && <p className="sim-banner" role="note">합성 시연 데이터입니다. 실제 가두리 값이 아닙니다.</p>}
      <main id="main" tabIndex={-1} className={isHome ? 'home' : 'container'}>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/farmer" element={<FarmerPage />} />
          <Route path="/stock" element={<StockPage />} />
          <Route path="/notifications" element={<NotificationsPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <SiteFooter />
      {!isHome && <DemoBar />}
      <LevelToast cageId={3} />
    </DemoProvider>
  );
}
