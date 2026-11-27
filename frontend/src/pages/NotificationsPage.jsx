import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useDemo } from '../demo/DemoContext.jsx';
import { FarmerTabs } from '../components/SiteChrome.jsx';
import { api } from '../api/client.js';
import { LEVELS } from '../constants.js';

const CAGE_ID = 3;

const day = (iso) => new Date(iso).toLocaleDateString('ko-KR', { month: 'long', day: 'numeric' });
const clock = (iso) => new Date(iso).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', hour12: false });

// 알림 모의 화면. 시연용이라 실제 발송(문자·메신저 등)은 하지 않는다.
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

  return (
    <div className="notif-page">
      <FarmerTabs />
      <header className="page-head">
        <h1>알림</h1>
        <p className="lead">가두리 {CAGE_ID}번의 단계가 바뀐 때마다 알림이 쌓입니다. 가장 최근 알림이 맨 위에 있습니다.</p>
      </header>

      <div className="notif-grid">
        <section aria-label="알림 목록">
          {error && <p className="error big">알림을 불러오지 못했습니다.</p>}
          {!error && !data && <p className="big">불러오는 중…</p>}
          {data && data.items.length === 0 && (
            <div className="card">
              <p className="empty">아직 알림이 없습니다. 단계가 바뀌면 여기에 쌓입니다.</p>
            </div>
          )}
          {data && data.items.length > 0 && (
            <ol className="timeline">
              {data.items.map((n) => {
                const lv = LEVELS[n.level];
                return (
                  <li key={n.id} className="tl-item">
                    <time className="tl-time" dateTime={n.ts}>
                      <span>{day(n.ts)}</span>
                      <strong>{clock(n.ts)}</strong>
                    </time>
                    <span className="tl-dot" style={{ background: lv.color }} aria-hidden="true" />
                    <article className="card tl-card" style={{ borderTopColor: lv.color }}>
                      <h2 style={{ color: lv.color }}>
                        <span aria-hidden="true">{lv.icon}</span> {n.level}
                      </h2>
                      <p>{n.text}</p>
                      {n.level !== '정상' && <Link to="/farmer#evidence">근거 그래프 보기</Link>}
                    </article>
                  </li>
                );
              })}
            </ol>
          )}
        </section>

        <aside className="card aside">
          <h2>알림은 이렇게 보냅니다</h2>
          <ul>
            <li>경보 단계가 바뀌면 알림이 만들어집니다. 단계가 올라가면 화면 위에도 바로 알려줍니다.</li>
            <li>같은 단계의 알림은 6시간에 한 번만 보냅니다.</li>
            <li>알림에는 의심되는 원인과 지금 할 일, 근거 그래프 링크가 들어갑니다.</li>
            <li>지금은 시연용 화면이라 실제 문자나 메신저 메시지는 발송되지 않습니다.</li>
          </ul>
        </aside>
      </div>
    </div>
  );
}
