import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useDemo } from '../demo/DemoContext.jsx';
import { api } from '../api/client.js';
import { LEVELS, LEVEL_RANK } from '../constants.js';

const POLL_MS = Number(import.meta.env.VITE_ALERT_POLL_MS) || 10000;

// 시연 시각이 바뀌거나 주기마다 단계를 확인해, 경보 단계가 올라가면 화면 위에 알림을 띄운다. (해커톤: 실제 푸시·문자 발송 대신 화면 알림)
export default function LevelToast({ cageId }) {
  const { demo } = useDemo();
  const prev = useRef(null);
  const lastScenario = useRef(demo.scenario);
  const [toast, setToast] = useState(null);

  useEffect(() => {
    let alive = true;
    // 시나리오를 바꾸면 비교 기준과 떠 있던 알림을 초기화한다.
    if (lastScenario.current !== demo.scenario) {
      lastScenario.current = demo.scenario;
      prev.current = null;
      setToast(null);
    }
    const check = async () => {
      try {
        const s = await api.getStatus(cageId, demo);
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
  }, [cageId, demo]);

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
