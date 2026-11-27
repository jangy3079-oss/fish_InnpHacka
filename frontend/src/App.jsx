import { NavLink, Navigate, Route, Routes } from 'react-router-dom';
import FarmerPage from './pages/FarmerPage.jsx';
import DashboardPage from './pages/DashboardPage.jsx';
import NotificationsPage from './pages/NotificationsPage.jsx';
import LevelToast from './components/LevelToast.jsx';

export default function App() {
  return (
    <>
      <header className="topbar">
        <strong>조피볼락 스트레스 조기경보</strong>
        <nav>
          <NavLink to="/farmer">어민 화면</NavLink>
          <NavLink to="/notifications">알림</NavLink>
          <NavLink to="/dashboard">지자체 대시보드</NavLink>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/farmer" replace />} />
          <Route path="/farmer" element={<FarmerPage />} />
          <Route path="/notifications" element={<NotificationsPage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="*" element={<Navigate to="/farmer" replace />} />
        </Routes>
      </main>
      <LevelToast cageId={3} />
    </>
  );
}
