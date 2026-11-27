import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client.js';
import { useDemo } from '../demo/DemoContext.jsx';
import { ErrorNotice, LoadingBlock, Skeleton } from './States.jsx';

// 작은 그래프 여러 개를 나란히 놓기 위한 기준 크기. 휴대폰 폭에서도 글자가 거의 원래 크기로 보인다.
const W = 340;
const H = 200;
const PAD = { l: 40, r: 10, t: 14, b: 44 };
const IW = W - PAD.l - PAD.r;
const IH = H - PAD.t - PAD.b;

const hourLabel = (iso) => `${String(new Date(iso).getHours()).padStart(2, '0')}시`;
const dayLabel = (iso) => new Date(iso).toLocaleDateString('ko-KR', { month: 'long', day: 'numeric' });
const dayKey = (iso) => new Date(iso).toDateString();

function makeScales(points, yMin, yMax) {
  const t0 = new Date(points[0].ts).getTime();
  const t1 = new Date(points[points.length - 1].ts).getTime();
  const x = (iso) => PAD.l + ((new Date(iso).getTime() - t0) / (t1 - t0 || 1)) * IW;
  const y = (v) => PAD.t + (1 - (v - yMin) / (yMax - yMin || 1)) * IH;
  return { x, y };
}

// 시간 축에 찍을 눈금: 4곳. 첫 눈금과 날짜가 바뀌는 눈금에는 날짜도 함께 쓴다.
function timeTicks(points) {
  const n = points.length;
  const idx = [...new Set([0, 1, 2, 3].map((k) => Math.round((k * (n - 1)) / 3)))];
  return idx.map((i, k) => ({
    i,
    showDay: k === 0 || dayKey(points[i].ts) !== dayKey(points[idx[k - 1]].ts),
  }));
}

function Axes({ points, x, y, yMin, yMax, unit, alertAt, fmt }) {
  const ticks = [yMin, (yMin + yMax) / 2, yMax];
  return (
    <>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} stroke="#e1e8ef" />
          <text x={PAD.l - 6} y={y(t) + 4} textAnchor="end" fontSize="11" fill="#5d6b77">
            {fmt ? fmt(t) : Number.isInteger(t) ? t : t.toFixed(1)}
            {unit}
          </text>
        </g>
      ))}
      {timeTicks(points).map(({ i, showDay }) => {
        const px = x(points[i].ts);
        const anchor = i === 0 ? 'start' : i === points.length - 1 ? 'end' : 'middle';
        return (
          <g key={points[i].ts}>
            <text x={px} y={H - 24} textAnchor={anchor} fontSize="11" fill="#5d6b77">{hourLabel(points[i].ts)}</text>
            {showDay && <text x={px} y={H - 9} textAnchor={anchor} fontSize="11" fill="#8896a1">{dayLabel(points[i].ts)}</text>}
          </g>
        );
      })}
      {alertAt && (
        <g>
          <line x1={x(alertAt)} x2={x(alertAt)} y1={PAD.t} y2={PAD.t + IH} stroke="#b3261e" strokeDasharray="4 3" strokeWidth="1.8" />
          <text x={x(alertAt) - 4} y={PAD.t + 10} textAnchor="end" fontSize="11" fontWeight="700" fill="#b3261e">경보</text>
        </g>
      )}
    </>
  );
}

function Legend({ items }) {
  return (
    <ul className="legend">
      {items.map((i) => (
        <li key={i.label}>
          <span className="swatch" style={{ background: i.color }} /> {i.label}
        </li>
      ))}
    </ul>
  );
}

// 마우스를 올리거나 누르면, 키보드는 좌우 화살표로 가장 가까운 시점을 고른다.
function useHover(points, x) {
  const [idx, setIdx] = useState(null);
  const nearest = (clientX, svg) => {
    const rect = svg.getBoundingClientRect();
    const px = ((clientX - rect.left) / rect.width) * W;
    let best = 0;
    let bd = Infinity;
    points.forEach((p, i) => {
      const d = Math.abs(x(p.ts) - px);
      if (d < bd) {
        bd = d;
        best = i;
      }
    });
    return best;
  };
  const handlers = {
    tabIndex: 0,
    onPointerMove: (e) => setIdx(nearest(e.clientX, e.currentTarget)),
    onPointerDown: (e) => setIdx(nearest(e.clientX, e.currentTarget)),
    onPointerLeave: () => setIdx(null),
    onBlur: () => setIdx(null),
    onKeyDown: (e) => {
      if (e.key === 'ArrowLeft') setIdx((i) => Math.max(0, (i ?? points.length) - 1));
      else if (e.key === 'ArrowRight') setIdx((i) => Math.min(points.length - 1, (i ?? -1) + 1));
      else if (e.key === 'Escape') setIdx(null);
      else return;
      e.preventDefault();
    },
  };
  return { idx, handlers };
}

// 값 안내 상자. figure 안에 절대 위치로 띄운다.
function Tip({ at, time, rows }) {
  if (!at) return null;
  const left = Math.min(80, Math.max(20, (at / W) * 100));
  return (
    <div className="chart-tip" style={{ left: `${left}%` }} role="status">
      <strong>{time}</strong>
      {rows.map((r) => (
        <span key={r.label}>
          <i className="swatch" style={{ background: r.color }} /> {r.label} {r.value}
        </span>
      ))}
    </div>
  );
}

const fmtTime = (iso) => `${dayLabel(iso)} ${hourLabel(iso)}`;

// 선 그래프. series: [{label, color, points:[{ts, value:number}], fmt}]
export function LineChart({ title, series, yMin, yMax, unit = '', alertAt }) {
  const base = series[0]?.points;
  const hasData = !!base?.length;
  const { x, y } = makeScales(hasData ? base : [{ ts: 0 }, { ts: 1 }], yMin, yMax);
  const { idx, handlers } = useHover(base ?? [], x);
  if (!hasData) return <p className="hint">{title}: 표시할 데이터가 없습니다.</p>;
  const at = idx != null ? x(base[idx].ts) : null;
  const last = series.map((s) => `${s.label} ${s.fmt(s.points[s.points.length - 1].value)}`).join(', ');
  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${title}. 가장 최근 값: ${last}. 좌우 화살표 키로 시점별 값을 볼 수 있습니다.`} {...handlers}>
        <Axes points={base} x={x} y={y} yMin={yMin} yMax={yMax} unit={unit} alertAt={alertAt} />
        {series.map((s) => (
          <polyline key={s.label} fill="none" stroke={s.color} strokeWidth="2.4" strokeLinejoin="round" points={s.points.map((p) => `${x(p.ts)},${y(p.value)}`).join(' ')} />
        ))}
        {idx != null && (
          <g>
            <line x1={at} x2={at} y1={PAD.t} y2={PAD.t + IH} stroke="#14212b" strokeOpacity="0.45" />
            {series.map((s) => (
              <circle key={s.label} cx={at} cy={y(s.points[idx].value)} r="4" fill="#fff" stroke={s.color} strokeWidth="2.2" />
            ))}
          </g>
        )}
      </svg>
      <Tip
        at={at}
        time={idx != null ? fmtTime(base[idx].ts) : ''}
        rows={idx != null ? series.map((s) => ({ label: s.label, color: s.color, value: s.fmt(s.points[idx].value) })) : []}
      />
      {series.length > 1 && <Legend items={series} />}
    </figure>
  );
}

const LAYERS = [
  { label: '저층', color: '#1d4e7a' },
  { label: '중층', color: '#35bfc0' },
  { label: '표층', color: '#f2c14e' },
];
const pct = (v) => `${Math.round(v * 100)}%`;

// 수직 분포: 값은 [저층, 중층, 표층] 비율(합 1)을 쌓은 면 그래프
export function VerticalDistChart({ points, alertAt }) {
  const hasData = !!points?.length;
  const { x, y } = makeScales(hasData ? points : [{ ts: 0 }, { ts: 1 }], 0, 1);
  const { idx, handlers } = useHover(points ?? [], x);
  if (!hasData) return <p className="hint">수직 분포: 표시할 데이터가 없습니다.</p>;
  const cum = points.map((p) => p.value.reduce((a, v) => [...a, (a.length ? a[a.length - 1] : 0) + v], []));
  const area = (k) => {
    const upper = points.map((p, i) => `${x(p.ts)},${y(cum[i][k])}`);
    const lower = points.map((p, i) => `${x(p.ts)},${y(k === 0 ? 0 : cum[i][k - 1])}`).reverse();
    return [...upper, ...lower].join(' ');
  };
  const at = idx != null ? x(points[idx].ts) : null;
  const lastV = points[points.length - 1].value;
  return (
    <figure className="chart">
      <figcaption>물고기가 있는 깊이</figcaption>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`물고기가 있는 깊이 비율. 가장 최근 값: 표층 ${pct(lastV[2])}, 중층 ${pct(lastV[1])}, 저층 ${pct(lastV[0])}. 좌우 화살표 키로 시점별 값을 볼 수 있습니다.`}
        {...handlers}
      >
        {LAYERS.map((l, k) => (
          <polygon key={l.label} points={area(k)} fill={l.color} opacity="0.88" />
        ))}
        <Axes points={points} x={x} y={y} yMin={0} yMax={1} unit="" alertAt={alertAt} fmt={pct} />
        {idx != null && <line x1={at} x2={at} y1={PAD.t} y2={PAD.t + IH} stroke="#14212b" strokeOpacity="0.55" />}
      </svg>
      <Tip
        at={at}
        time={idx != null ? fmtTime(points[idx].ts) : ''}
        rows={idx != null ? [2, 1, 0].map((j) => ({ label: LAYERS[j].label, color: LAYERS[j].color, value: pct(points[idx].value[j]) })) : []}
      />
      <Legend items={[...LAYERS].reverse()} />
    </figure>
  );
}

function useTimeseries(cageId, metric) {
  const { demo } = useDemo();
  const [data, setData] = useState(null);
  useEffect(() => {
    let alive = true;
    api
      .getTimeseries(cageId, metric, { demo })
      .then((d) => alive && setData(d))
      .catch(() => alive && setData({ points: [], failed: true }));
    return () => {
      alive = false;
    };
  }, [cageId, metric, demo]);
  return data;
}

// 알림에서 '#evidence' 링크로 들어오면 그래프가 그려진 뒤 해당 위치로 이동한다.
function scrollToHash(node) {
  if (node && window.location.hash === '#evidence') node.scrollIntoView();
}

export default function EvidenceCharts({ cageId, alertAt }) {
  const vd = useTimeseries(cageId, 'vertical_dist');
  const act = useTimeseries(cageId, 'activity');
  const ts = useTimeseries(cageId, 'temp_surface');
  const tm = useTimeseries(cageId, 'temp_mid');

  if (!vd || !act || !ts || !tm) {
    return (
      <section className="card flat" id="evidence">
        <h2>판단 근거 그래프</h2>
        <LoadingBlock label="그래프를 불러오는 중입니다">
          <div className="chart-grid">
            <Skeleton height={230} />
            <Skeleton height={230} />
            <Skeleton height={230} />
            <Skeleton height={230} />
          </div>
        </LoadingBlock>
      </section>
    );
  }
  if ([vd, act, ts, tm].some((d) => d.failed)) {
    return (
      <section className="card flat" id="evidence">
        <h2>판단 근거 그래프</h2>
        <ErrorNotice message="그래프를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요." />
      </section>
    );
  }

  if (vd.points.length < 2) {
    return (
      <section className="card flat" id="evidence" ref={scrollToHash}>
        <h2>판단 근거 그래프</h2>
        <p className="hint">아직 그래프를 그릴 데이터가 모이지 않았습니다.</p>
        <Echogram cageId={cageId} />
      </section>
    );
  }

  // 활동량은 정상일 때 1.0 안팎이라 축을 0~1로 고정하지 않고 데이터에 맞춘다.
  const activityMax = Math.max(1, Math.ceil(Math.max(...act.points.map((p) => p.value)) * 5) / 5);
  const temps = [...ts.points, ...tm.points].map((p) => p.value);
  const tMin = Math.floor(Math.min(...temps)) - 1;
  const tMax = Math.ceil(Math.max(...temps)) + 1;

  return (
    <section className="card flat" id="evidence" ref={scrollToHash}>
      <h2>판단 근거 그래프</h2>
      <p className="hint">그래프에 마우스를 올리거나 누르면 그 시각의 값을 볼 수 있습니다.</p>
      <div className="chart-grid">
        <VerticalDistChart points={vd.points} alertAt={alertAt} />
        <LineChart
          title="움직임 (평소 대비)"
          series={[{ label: '활동량', color: '#13243b', points: act.points, fmt: (v) => v.toFixed(2) }]}
          yMin={0}
          yMax={activityMax}
          alertAt={alertAt}
        />
        <LineChart
          title="층별 수온"
          series={[
            { label: '표층', color: '#d9480f', points: ts.points, fmt: (v) => `${v.toFixed(1)}℃` },
            { label: '중층', color: '#1971c2', points: tm.points, fmt: (v) => `${v.toFixed(1)}℃` },
          ]}
          yMin={tMin}
          yMax={tMax}
          unit="℃"
          alertAt={alertAt}
        />
        <Echogram cageId={cageId} />
      </div>
    </section>
  );
}

function Echogram({ cageId }) {
  const { demo } = useDemo();
  const [failed, setFailed] = useState(false);
  const ref = useRef(null);
  return (
    <figure className="chart" ref={ref}>
      <figcaption>소나 에코그램</figcaption>
      {failed ? (
        <p className="hint">이미지를 불러오지 못했습니다.</p>
      ) : (
        <img className="echogram" src={api.getEchogramUrl(cageId, { demo })} alt="시간에 따른 물고기 깊이 분포 에코그램" onError={() => setFailed(true)} />
      )}
    </figure>
  );
}
