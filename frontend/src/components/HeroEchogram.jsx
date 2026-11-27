import scenarios from '../mocks/scenarios.json';
import { DEFAULT_DEMO } from '../mocks/demo.js';
import { LEVELS, LEVEL_RANK } from '../constants.js';

// 첫 화면의 에코그램. 기본 시나리오의 수직 분포 시계열에서 어군의 깊이를 계산해 그린다.
// 경고·위험 시점, 시간 라벨, 설명 문구는 모두 데이터에서 구한다. 값이 없으면 그 표시와 문구를 생략한다.
const W = 760;
const H = 342;
const PAD = { l: 46, r: 14, t: 30, b: 46 };
const IW = W - PAD.l - PAD.r;
const IH = H - PAD.t - PAD.b;
const MAX_DEPTH = 9; // 그물 깊이(m). 표층 0~3, 중층 3~6, 저층 6~9
const ZONE_CENTER = [7.5, 4.5, 1.5]; // 값 순서 [저층, 중층, 표층]

const scenario = scenarios[DEFAULT_DEMO.scenario];
const frames = scenario.frames;
// 앞부분에 값이 없는(null) 점은 그릴 수 없으니 뺀다.
const pts = scenario.series.vertical_dist.filter((p) => Array.isArray(p.value) && p.value.length === 3);
const n = pts.length;
const x = (i) => PAD.l + (n > 1 ? (i / (n - 1)) * IW : 0);
const y = (d) => PAD.t + (d / MAX_DEPTH) * IH;
const idxByTs = new Map(pts.map((p, i) => [p.ts, i]));

// 시점마다 어군의 중심 깊이와 퍼진 정도
const school = pts.map((p) => {
  const center = p.value.reduce((s, v, j) => s + v * ZONE_CENTER[j], 0);
  const spread = Math.sqrt(p.value.reduce((s, v, j) => s + v * (ZONE_CENTER[j] - center) ** 2, 0) + 0.3);
  return { center, half: Math.max(0.45, spread * 0.55) };
});

const band = (scale) => {
  const top = school.map((s, i) => `${x(i)},${y(s.center - s.half * scale)}`);
  const bottom = school.map((s, i) => `${x(i)},${y(s.center + s.half * scale)}`).reverse();
  return [...top, ...bottom].join(' ');
};

// 개체 신호처럼 보이는 점들. 값이 매번 같게 고정 시드를 쓴다.
const dots = (() => {
  let seed = 7;
  const rnd = () => {
    seed = (seed * 16807) % 2147483647;
    return seed / 2147483647;
  };
  return Array.from({ length: n ? 150 : 0 }, () => {
    const i = Math.floor(rnd() * n);
    const s = school[i];
    const offset = (rnd() + rnd() + rnd() - 1.5) * s.half * 2.4;
    return { cx: x(i) + (rnd() - 0.5) * 8, cy: y(Math.min(MAX_DEPTH, Math.max(0, s.center + offset))), r: 0.8 + rnd() * 1.1, o: 0.25 + rnd() * 0.5 };
  });
})();

// 처음 '경고', '위험'이 되는 프레임의 시점(그래프에 그려진 점과 같은 시각일 때만 표시)
const MARKERS = ['경고', '위험']
  .map((level) => {
    const f = frames.find((fr) => (LEVEL_RANK[fr.level] ?? -1) >= LEVEL_RANK[level]);
    const i = f ? idxByTs.get(f.ts) : undefined;
    return i === undefined ? null : { level, i };
  })
  .filter(Boolean);

// 표식 글자 상자의 가운데 위치. 표식이 서로 가까우면(상자 폭 52 + 여백) 선은 그대로 두고 상자만 좌우로 벌린다.
const labelX = (() => {
  const xs = MARKERS.map((m) => x(m.i));
  const need = 58;
  if (xs.length === 2 && xs[1] - xs[0] < need) {
    const push = (need - (xs[1] - xs[0])) / 2;
    return [xs[0] - push, xs[1] + push];
  }
  return xs;
})();

const hourLabel = (iso) => `${String(new Date(iso).getHours()).padStart(2, '0')}시`;
const dayLabel = (iso) => new Date(iso).toLocaleDateString('ko-KR', { month: 'long', day: 'numeric' });
const dayKey = (iso) => new Date(iso).toDateString();
const timeTicks = (() => {
  const idx = [...new Set(Array.from({ length: Math.min(6, n) }, (_, k) => Math.round((k * (n - 1)) / Math.max(1, Math.min(6, n) - 1))))];
  return idx.map((i, k) => ({ i, showDay: k === 0 || dayKey(pts[i].ts) !== dayKey(pts[idx[k - 1]].ts) }));
})();

// 설명 문구: 경고 시점까지의 표층 수온 변화와 어군 깊이 변화를 데이터로 확인한 뒤 쓴다.
const caption = (() => {
  const base = '시뮬레이션 데이터입니다.';
  const warn = MARKERS.find((m) => m.level === '경고');
  const danger = MARKERS.find((m) => m.level === '위험');
  if (!warn) return `${scenario.label ?? '시나리오'} 예시. ${base}`;
  const temps = (scenario.series.temp_surface ?? []).filter((p) => typeof p.value === 'number');
  const t0 = temps[0]?.value;
  const warnTs = pts[warn.i].ts;
  const tWarn = temps.filter((p) => new Date(p.ts) <= new Date(warnTs)).pop()?.value;
  const deeper = school[warn.i].center - school[0].center > 0.3;
  const move = deeper ? '어군이 아래층으로 내려가고' : '어군의 깊이가 바뀌고';
  const rise = typeof t0 === 'number' && typeof tWarn === 'number' && tWarn > t0 ? `표층 수온이 ${t0.toFixed(1)}℃에서 ${tWarn.toFixed(1)}℃로 오르자 ` : '';
  const levels = danger ? '단계가 경고에서 위험으로 올라갑니다.' : '단계가 경고로 올라갑니다.';
  return `${rise}${move}, ${levels} ${base}`;
})();

export default function HeroEchogram() {
  if (n < 2) return null; // 그릴 데이터가 없으면 소개 문구만 보인다
  return (
    <figure className="echo">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${scenario.label ?? '시나리오'}에서 시간이 지남에 따라 어군의 깊이가 변하는 소나 에코그램`}>
        <defs>
          <linearGradient id="water" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#2c6e8a" />
            <stop offset="1" stopColor="#071722" />
          </linearGradient>
          <clipPath id="reveal">
            <rect className="reveal-rect" x="0" y="0" width={W} height={H} />
          </clipPath>
        </defs>
        <rect x={PAD.l} y={PAD.t} width={IW} height={IH} fill="url(#water)" rx="2" />

        {[0, 3, 6, 9].map((d) => (
          <g key={d}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(d)} y2={y(d)} stroke="#9fc4d4" strokeOpacity={d === 0 || d === 9 ? 0 : 0.35} strokeDasharray="3 5" />
            <text x={PAD.l - 8} y={y(d) + 4} textAnchor="end" fontSize="12" fill="#5b7686">{d}m</text>
          </g>
        ))}
        {['표층', '중층', '저층'].map((z, k) => (
          <text key={z} x={PAD.l + 10} y={y(k * 3 + 1.4)} fontSize="12" fill="#cfe3ec" fillOpacity="0.7">{z}</text>
        ))}

        <g clipPath="url(#reveal)">
          <polygon points={band(1.5)} fill="#e8873a" fillOpacity="0.5" />
          <polygon points={band(1)} fill="#f2a93b" fillOpacity="0.75" />
          <polygon points={band(0.45)} fill="#f6d365" />
          {dots.map((d, k) => (
            <circle key={k} cx={d.cx} cy={d.cy} r={d.r} fill="#fbeab4" fillOpacity={d.o} />
          ))}
          {MARKERS.map((m, k) => {
            const lv = LEVELS[m.level];
            const lx = labelX[k];
            return (
              <g key={m.level}>
                <line x1={x(m.i)} x2={x(m.i)} y1={PAD.t} y2={PAD.t + IH} stroke="#fff" strokeWidth="1.5" strokeDasharray="5 4" />
                <rect x={lx - 26} y={PAD.t - 24} width="52" height="22" rx="2" fill={lv.color} />
                <text x={lx} y={PAD.t - 9} textAnchor="middle" fontSize="13" fontWeight="700" fill="#fff">{m.level}</text>
              </g>
            );
          })}
        </g>

        {timeTicks.map(({ i, showDay }) => {
          const anchor = i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'; // 양 끝 글자가 잘리지 않게
          return (
            <g key={pts[i].ts}>
              <text x={x(i)} y={H - 24} textAnchor={anchor} fontSize="12" fill="#5b7686">{hourLabel(pts[i].ts)}</text>
              {showDay && <text x={x(i)} y={H - 9} textAnchor={anchor} fontSize="12" fill="#7d96a5">{dayLabel(pts[i].ts)}</text>}
            </g>
          );
        })}
      </svg>
      <figcaption>{caption}</figcaption>
    </figure>
  );
}
