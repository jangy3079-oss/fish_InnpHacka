import { useEffect, useState } from 'react';
import { useDemo } from '../demo/DemoContext.jsx';
import { usePageTitle } from '../usePageTitle.js';
import { ErrorNotice, LoadingBlock, Skeleton } from '../components/States.jsx';
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
  const [attempt, setAttempt] = useState(0);
  usePageTitle('지자체 대시보드');

  const { demo, setBusy } = useDemo();

  // 해역 경계는 시연 시각과 상관없다. 데이터 출처(파일/서버)가 바뀔 때만 다시 받는다.
  // 아직 API에 없을 수 있어, 실패해도 표와 차트는 보여준다.
  const fromFile = demo?.file === true;
  useEffect(() => {
    let alive = true;
    api.getAreaShapes(demo).catch(() => null).then((s) => alive && setShapes(s));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fromFile, attempt]);

  // 시나리오·시각이 바뀌면 다시 불러온다. 이전 화면은 새 값이 올 때까지 유지한다.
  // 비우고 다시 그리면 지도가 통째로 새로 만들어져 재생 중 화면이 깜빡인다.
  useEffect(() => {
    let alive = true;
    setBusy(true); // 응답이 올 때까지 재생을 멈춘다
    const timer = setTimeout(() => {
      api.getStressMap(demo)
        .then((m) => {
          if (!alive) return;
          setBusy(false);
          setMap(m);
          setError(null);
        })
        .catch((e) => {
          if (!alive) return;
          setBusy(false);
          setError(e.message);
        });
    }, api.debounceFor(demo));
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [demo, attempt, setBusy]);

  // 화면을 떠나면 재생이 멈춘 채로 남지 않게 푼다.
  useEffect(() => () => setBusy(false), [setBusy]);

  if (error && !map) return <ErrorNotice onRetry={() => { setError(null); setAttempt((a) => a + 1); }} />;
  if (!map) {
    return (
      <LoadingBlock label="대시보드를 불러오는 중입니다">
        <Skeleton height={36} width="260px" />
        <div style={{ height: 12 }} />
        <Skeleton height={380} />
        <div style={{ height: 14 }} />
        <Skeleton height={200} />
      </LoadingBlock>
    );
  }

  const counts = Object.keys(LEVELS).map((lv) => ({ lv, n: map.areas.filter((a) => a.level === lv).length }));

  return (
    <div className="dash">
      <h1>해역 스트레스 현황</h1>
      <p className="hint">{formatTime(map.ts)} 기준 · 해역 단위 집계입니다. 단계·지수는 유효한 최신 회차의 최댓값이며 자료 부족으로 유지된 단계는 별도 표시합니다.</p>

      <ul className="summary">
        {counts.map(({ lv, n }) => (
          <li key={lv} style={{ background: LEVELS[lv].bg, color: LEVELS[lv].color }}>
            <span aria-hidden="true">{LEVELS[lv].icon}</span> {lv} <strong>{n}</strong>
          </li>
        ))}
        <li>미집계 <strong>{map.areas.filter((a) => a.level == null).length}</strong></li>
      </ul>

      <div className="dash-grid">
        <section className="card">
          <h2>해역 지도</h2>
          {shapes?.features?.length ? (
            <AreaMap shapes={shapes} areas={map.areas} selectedId={selected} onSelect={setSelected} />
          ) : (
            <p className="hint">해역 경계 데이터를 아직 받을 수 없어 지도를 표시하지 못합니다. 오른쪽 표를 확인해 주세요.</p>
          )}
        </section>

        <section className="card">
          <h2>해역별 단계</h2>
          <table className="areas">
            <thead>
              <tr><th>해역</th><th>단계</th><th>지수</th><th>가두리</th></tr>
            </thead>
            <tbody>
              {map.areas.map((a) => (
                <tr
                  key={a.area_id}
                  className={a.area_id === selected ? 'selected' : ''}
                  aria-current={a.area_id === selected ? 'true' : undefined}
                  onClick={() => setSelected((cur) => (cur === a.area_id ? null : a.area_id))}
                >
                  <td>
                    <button
                      type="button"
                      className="row-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        setSelected((cur) => (cur === a.area_id ? null : a.area_id));
                      }}
                    >
                      {a.name}
                    </button>
                  </td>
                  <td style={{ color: LEVELS[a.level]?.color ?? '#64748b', fontWeight: 700 }}>
                    {LEVELS[a.level]?.icon} {a.level ?? '미집계'}
                  </td>
                  <td>{a.stress_score == null ? '미집계' : Math.round(a.stress_score)}</td>
                  <td>{a.cage_count}
                    {a.data_status && <small className="hint"> · 집계 {a.assessed_cage_count} / 단계 유지 {a.held_cage_count} / 미집계 {a.unavailable_cage_count}</small>}
                  </td>
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
      <p className="hint">유효한 최신 분석에서 원인이 표시된 경보 가두리 {total}곳 기준입니다. 원인 미확인·관측 부족은 제외하며 복수 후보는 첫 순위로 집계합니다.</p>
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
