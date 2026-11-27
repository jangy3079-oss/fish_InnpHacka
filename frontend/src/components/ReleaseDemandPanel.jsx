import { useEffect, useState } from 'react';
import { api } from '../api/client.js';
import { useDemo } from '../demo/DemoContext.jsx';

const BASELINE = 100000; // 방류 수요 기준선 (10만 마리)
const fmt = (n) => n.toLocaleString('ko-KR');

// 해역별 방류 예상 마릿수와 기준선. 해역 단위 집계만 보여주고 개별 어가 값은 없다. (FR-DEL-05)
export default function ReleaseDemandPanel({ areas }) {
  const { demo } = useDemo();
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    Promise.all(areas.map((a) => api.getReleaseDemand(a.area_id, demo).then((d) => ({ ...d, name: a.name }))))
      .then((r) => alive && (setRows(r), setError(false)))
      .catch(() => alive && setError(true));
    return () => {
      alive = false;
    };
  }, [areas, demo]);

  if (error) return <section className="card"><h2>방류 수요 예측</h2><p className="error">불러오지 못했습니다.</p></section>;
  if (!rows) return null;

  const max = Math.max(BASELINE * 1.2, ...rows.map((r) => r.estimated_fish_count));
  const pct = (n) => `${(n / max) * 100}%`;

  return (
    <section className="card">
      <h2>방류 수요 예측</h2>
      <p className="hint">해역별 방류가 필요할 것으로 예상되는 물고기 수입니다. 점선은 기준선 {fmt(BASELINE)}마리입니다.</p>
      <ul className="demand">
        {rows.map((r) => {
          const over = r.estimated_fish_count >= BASELINE;
          return (
            <li key={r.area_id}>
              <div className="demand-head">
                <strong>{r.name}</strong>
                <span className={over ? 'over' : ''}>
                  {fmt(r.estimated_fish_count)}마리{over && ' · 기준선 초과'}
                </span>
              </div>
              <div className="demand-track" role="img" aria-label={`${r.name} 방류 예상 ${fmt(r.estimated_fish_count)}마리`}>
                <span className={over ? 'demand-fill over' : 'demand-fill'} style={{ width: pct(r.estimated_fish_count) }} />
                <span className="demand-base" style={{ left: pct(BASELINE) }} />
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
