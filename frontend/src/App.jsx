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

export default function App() {
  const { pathname } = useLocation();
  const isHome = pathname === '/';
  return (
    <DemoProvider>
      <SiteHeader />
      <main className={isHome ? 'home' : 'container'}>
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
