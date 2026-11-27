import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client.js';
import { LEVELS, LEVEL_RANK } from '../constants.js';

const POLL_MS = Number(import.meta.env.VITE_ALERT_POLL_MS) || 10000;

// 경보 단계가 올라가면 화면 위에 알림을 띄운다. (해커톤: 실제 푸시·문자 발송 대신 화면 알림)
export default function LevelToast({ cageId }) {
  const prev = useRef(null);
  const [toast, setToast] = useState(null);

  useEffect(() => {
    let alive = true;
    const check = async () => {
      try {
        const s = await api.getStatus(cageId);
        if (!alive || s.level == null) return;
        if (prev.current != null && LEVEL_RANK[s.level] > LEVEL_RANK[prev.current]) {
          setToast({ level: s.level, from: prev.current });
        }
        prev.current = s.level;
      } catch {
        /* 다음 주기에 다시 시도 */
      }
    };
    check();
    const t = setInterval(check, POLL_MS);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [cageId]);

  if (!toast) return null;
  const lv = LEVELS[toast.level];
  return (
    <div className="toast" role="alert" style={{ borderColor: lv.color }}>
      <strong style={{ color: lv.color }}>
        {lv.icon} {cageId}번 가두리 {toast.from} → {toast.level}
      </strong>
      <p>{lv.message}</p>
      <div className="toast-actions">
        <Link to="/notifications" onClick={() => setToast(null)}>알림 보기</Link>
        <button onClick={() => setToast(null)}>닫기</button>
      </div>
    </div>
  );
}
