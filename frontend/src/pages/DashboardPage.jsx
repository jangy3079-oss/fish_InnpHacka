import { useEffect, useState } from 'react';
import { useDemo } from '../demo/DemoContext.jsx';
import { api } from '../api/client.js';
import AreaMap from '../components/AreaMap.jsx';
import ReleaseDemandPanel from '../components/ReleaseDemandPanel.jsx';
import { LEVELS, formatTime } from '../constants.js';

// 지자체 대시보드: 해역 단위 집계만 보여준다. 개별 어가의 손익·재고는 표시하지 않는다.
export default function DashboardPage() {
  const [map, setMap] = useState(null);
  const [shapes, setShapes] = useState(null);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState(null);

  const { demo } = useDemo();

  useEffect(() => {
    let alive = true;
    Promise.all([api.getStressMap(demo), api.getAreaShapes()])
      .then(([m, s]) => {
        if (!alive) return;
        setMap(m);
        setShapes(s);
      })
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [demo]);

  if (error) return <p className="error big">불러오지 못했습니다. 잠시 후 다시 시도해 주세요.</p>;
  if (!map || !shapes) return <p className="big">불러오는 중…</p>;

  const counts = Object.keys(LEVELS).map((lv) => ({ lv, n: map.areas.filter((a) => a.level === lv).length }));

  return (
    <div className="dash">
      <h1>해역 스트레스 현황</h1>
      <p className="hint">{formatTime(map.ts)} 기준 · 해역 단위 집계입니다.</p>

      <ul className="summary">
        {counts.map(({ lv, n }) => (
          <li key={lv} style={{ background: LEVELS[lv].bg, color: LEVELS[lv].color }}>
            <span aria-hidden="true">{LEVELS[lv].icon}</span> {lv} <strong>{n}</strong>
          </li>
        ))}
      </ul>

      <div className="dash-grid">
        <section className="card">
          <h2>해역 지도</h2>
          <AreaMap shapes={shapes} areas={map.areas} selectedId={selected} onSelect={setSelected} />
        </section>

        <section className="card">
          <h2>해역별 단계</h2>
          <table className="areas">
            <thead>
              <tr><th>해역</th><th>단계</th><th>지수</th><th>가두리</th></tr>
            </thead>
            <tbody>
              {map.areas.map((a) => (
                <tr key={a.area_id} className={a.area_id === selected ? 'selected' : ''} onClick={() => setSelected(a.area_id)}>
                  <td>{a.name}</td>
                  <td style={{ color: LEVELS[a.level].color, fontWeight: 700 }}>
                    {LEVELS[a.level].icon} {a.level}
                  </td>
                  <td>{Math.round(a.stress_score)}</td>
                  <td>{a.cage_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>

      <ReleaseDemandPanel areas={map.areas} />
      <CauseChart items={map.cause_distribution} />
    </div>
  );
}

function CauseChart({ items }) {
  if (!items?.length) return null;
  const max = Math.max(...items.map((i) => i.count));
  const total = items.reduce((s, i) => s + i.count, 0);
  return (
    <section className="card">
      <h2>의심 원인 분포</h2>
      <p className="hint">경보 중인 가두리 {total}곳 기준 (복수 원인은 가장 가능성 높은 것으로 집계)</p>
      <ul className="bars">
        {items.map((i) => (
          <li key={i.name}>
            <span className="bar-label">{i.name}</span>
            <span className="bar-track"><span className="bar-fill" style={{ width: `${(i.count / max) * 100}%` }} /></span>
            <span className="bar-n">{i.count}곳</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
