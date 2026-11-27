import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useDemo } from '../demo/DemoContext.jsx';
import { FarmerTabs } from '../components/SiteChrome.jsx';
import { api } from '../api/client.js';
import { LEVELS, formatTime } from '../constants.js';

const CAGE_ID = 3;

// 메신저 모양의 알림 모의 화면. 실제 발송(카카오·문자 등)은 하지 않는다.
export default function NotificationsPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);

  const { demo } = useDemo();

  useEffect(() => {
    let alive = true;
    api.getNotifications(CAGE_ID, demo).then((d) => alive && setData(d)).catch(() => alive && setError(true));
    return () => {
      alive = false;
    };
  }, [demo]);

  if (error) return <p className="error big">알림을 불러오지 못했습니다.</p>;
  if (!data) return <p className="big">불러오는 중…</p>;

  return (
    <div className="narrow">
    <FarmerTabs />
    <div className="chat">
      <h1>알림</h1>
      <p className="hint">시연용 모의 화면입니다. 실제 메시지는 발송되지 않습니다.</p>
      <ul className="bubbles">
        {data.items.map((n) => {
          const lv = LEVELS[n.level];
          return (
            <li key={n.id} className="bubble" style={{ borderLeftColor: lv.color }}>
              <div className="bubble-head">
                <strong style={{ color: lv.color }}>{lv.icon} {n.level}</strong>
                <time dateTime={n.ts}>{formatTime(n.ts)}</time>
              </div>
              <p>{n.text}</p>
              {n.level !== '정상' && <Link to="/farmer#evidence">근거 그래프 보기 ›</Link>}
            </li>
          );
        })}
      </ul>
    </div>
    </div>
  );
}
