import { useEffect, useState } from 'react';
import { api } from '../api/client.js';
import { useDemo } from '../demo/DemoContext.jsx';
import { ErrorNotice, LoadingBlock, Skeleton } from './States.jsx';

const W = 480;
const H = 170;
const PAD = { l: 40, r: 12, t: 12, b: 26 };
const IW = W - PAD.l - PAD.r;
const IH = H - PAD.t - PAD.b;

const hour = (iso) => `${String(new Date(iso).getHours()).padStart(2, '0')}시`;

function makeScales(points, yMin, yMax) {
  const t0 = new Date(points[0].ts).getTime();
  const t1 = new Date(points[points.length - 1].ts).getTime();
  const x = (iso) => PAD.l + ((new Date(iso).getTime() - t0) / (t1 - t0 || 1)) * IW;
  const y = (v) => PAD.t + (1 - (v - yMin) / (yMax - yMin || 1)) * IH;
  return { x, y };
}

function Axes({ points, x, y, yMin, yMax, unit, alertAt }) {
  const ticks = [yMin, (yMin + yMax) / 2, yMax];
  const step = Math.max(1, Math.floor(points.length / 5));
  return (
    <>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} stroke="#e1e8ef" />
          <text x={PAD.l - 6} y={y(t) + 4} textAnchor="end" fontSize="11" fill="#6b7785">
            {Number.isInteger(t) ? t : t.toFixed(1)}{unit}
          </text>
        </g>
      ))}
      {points.map((p, i) =>
        i % step === 0 ? (
          <text key={p.ts} x={x(p.ts)} y={H - 8} textAnchor="middle" fontSize="11" fill="#6b7785">
            {hour(p.ts)}
          </text>
        ) : null,
      )}
      {alertAt && (
        <g>
          <line x1={x(alertAt)} x2={x(alertAt)} y1={PAD.t} y2={PAD.t + IH} stroke="#b3261e" strokeDasharray="4 3" strokeWidth="2" />
          <text x={x(alertAt) - 4} y={PAD.t + 10} textAnchor="end" fontSize="11" fontWeight="700" fill="#b3261e">
            경보
          </text>
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

// 선 그래프. series: [{label, color, points:[{ts, value:number}]}]
export function LineChart({ title, series, yMin, yMax, unit = '', alertAt }) {
  const base = series[0]?.points;
  if (!base?.length) return <p className="hint">{title}: 표시할 데이터가 없습니다.</p>;
  const { x, y } = makeScales(base, yMin, yMax);
  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title}>
        <Axes points={base} x={x} y={y} yMin={yMin} yMax={yMax} unit={unit} alertAt={alertAt} />
        {series.map((s) => (
          <polyline
            key={s.label}
            fill="none"
            stroke={s.color}
            strokeWidth="2.5"
            strokeLinejoin="round"
            points={s.points.map((p) => `${x(p.ts)},${y(p.value)}`).join(' ')}
          />
        ))}
      </svg>
      {series.length > 1 && <Legend items={series} />}
    </figure>
  );
}

const LAYERS = [
  { label: '저층', color: '#1d4e7a' },
  { label: '중층', color: '#35bfc0' },
  { label: '표층', color: '#f2c14e' },
];

// 수직 분포: 값은 [저층, 중층, 표층] 비율(합 1)을 쌓은 면 그래프
export function VerticalDistChart({ points, alertAt }) {
  if (!points?.length) return <p className="hint">수직 분포: 표시할 데이터가 없습니다.</p>;
  const { x, y } = makeScales(points, 0, 1);
  const cum = points.map((p) => p.value.reduce((a, v) => [...a, (a.length ? a[a.length - 1] : 0) + v], []));
  const area = (k) => {
    const upper = points.map((p, i) => `${x(p.ts)},${y(cum[i][k])}`);
    const lower = points
      .map((p, i) => `${x(p.ts)},${y(k === 0 ? 0 : cum[i][k - 1])}`)
      .reverse();
    return [...upper, ...lower].join(' ');
  };
  return (
    <figure className="chart">
      <figcaption>물고기가 있는 깊이 (비율)</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="수직 분포">
        {LAYERS.map((l, k) => (
          <polygon key={l.label} points={area(k)} fill={l.color} opacity="0.85" />
        ))}
        <Axes points={points} x={x} y={y} yMin={0} yMax={1} unit="" alertAt={alertAt} />
      </svg>
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
      <section className="card" id="evidence">
        <h2>왜 이렇게 판단했나요?</h2>
        <LoadingBlock label="그래프를 불러오는 중입니다">
          <Skeleton height={170} />
          <div style={{ height: 14 }} />
          <Skeleton height={170} />
        </LoadingBlock>
      </section>
    );
  }
  if ([vd, act, ts, tm].some((d) => d.failed)) {
    return (
      <section className="card" id="evidence">
        <h2>왜 이렇게 판단했나요?</h2>
        <ErrorNotice message="그래프를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요." />
      </section>
    );
  }

  if (vd.points.length < 2) return (
      <section className="card" id="evidence" ref={scrollToHash}>
        <h2>왜 이렇게 판단했나요?</h2>
        <p className="hint">아직 그래프를 그릴 데이터가 모이지 않았습니다.</p>
        <Echogram cageId={cageId} />
      </section>
    );

  // 활동량은 정상일 때 1.0 안팎이라 축을 0~1로 고정하지 않고 데이터에 맞춘다.
  const activityMax = Math.max(1, Math.ceil(Math.max(...act.points.map((p) => p.value)) * 5) / 5);
  const temps = [...ts.points, ...tm.points].map((p) => p.value);
  const tMin = Math.floor(Math.min(...temps)) - 1;
  const tMax = Math.ceil(Math.max(...temps)) + 1;

  return (
    <section className="card" id="evidence" ref={scrollToHash}>
      <h2>왜 이렇게 판단했나요?</h2>
      <VerticalDistChart points={vd.points} alertAt={alertAt} />
      <LineChart title="움직임 (평소 대비 활발한 정도)" series={[{ label: '활동량', color: '#13243b', points: act.points }]} yMin={0} yMax={activityMax} alertAt={alertAt} />
      <LineChart
        title="층별 수온 (℃)"
        series={[
          { label: '표층 수온', color: '#d9480f', points: ts.points },
          { label: '중층 수온', color: '#1971c2', points: tm.points },
        ]}
        yMin={tMin}
        yMax={tMax}
        unit="℃"
        alertAt={alertAt}
      />
      <Echogram cageId={cageId} />
    </section>
  );
}

function Echogram({ cageId }) {
  const [failed, setFailed] = useState(false);
  return (
    <figure className="chart">
      <figcaption>물고기 위치 영상 (소나 에코그램)</figcaption>
      {failed ? (
        <p className="hint">이미지를 불러오지 못했습니다.</p>
      ) : (
        <img className="echogram" src={api.getEchogramUrl(cageId)} alt="시간에 따른 물고기 깊이 분포 에코그램" onError={() => setFailed(true)} />
      )}
    </figure>
  );
}
