import scenarios from '../mocks/scenarios.json';
import { LEVELS } from '../constants.js';

// 첫 화면의 에코그램. 고수온·태풍 시나리오의 수직 분포 시계열에서 어군의 깊이를 계산해 그린다.
const W = 760;
const H = 330;
const PAD = { l: 46, r: 14, t: 30, b: 34 };
const IW = W - PAD.l - PAD.r;
const IH = H - PAD.t - PAD.b;
const MAX_DEPTH = 9; // 그물 깊이(m). 표층 0~3, 중층 3~6, 저층 6~9
const ZONE_CENTER = [7.5, 4.5, 1.5]; // 값 순서 [저층, 중층, 표층]
const MARKERS = [
  { t: 23, level: '경고' },
  { t: 31, level: '위험' },
];

const series = scenarios.heatwave_typhoon.series;
const vd = series.vertical_dist;
const n = vd.length;
const x = (i) => PAD.l + (i / (n - 1)) * IW;
const y = (d) => PAD.t + (d / MAX_DEPTH) * IH;

// 시점마다 어군의 중심 깊이와 퍼진 정도
const school = vd.map((p) => {
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
  return Array.from({ length: 150 }, () => {
    const i = Math.floor(rnd() * n);
    const s = school[i];
    const offset = (rnd() + rnd() + rnd() - 1.5) * s.half * 2.4;
    return { cx: x(i) + (rnd() - 0.5) * 8, cy: y(Math.min(MAX_DEPTH, Math.max(0, s.center + offset))), r: 0.8 + rnd() * 1.1, o: 0.25 + rnd() * 0.5 };
  });
})();

const KST_HOURS = (i) => (6 + i + 9) % 24; // 시나리오는 UTC 06시 시작
const timeLabels = [0, 6, 12, 18, 24, 30, 35];
const t0 = series.temp_surface[0].value;
const tAlert = series.temp_surface[23].value;

export default function HeroEchogram() {
  return (
    <figure className="echo">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="고수온·태풍 시나리오에서 시간이 지날수록 어군이 표층에서 중층과 저층으로 내려가는 소나 에코그램">
        <defs>
          <linearGradient id="water" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#2c6e8a" />
            <stop offset="1" stopColor="#071722" />
          </linearGradient>
          <clipPath id="reveal">
            <rect className="reveal-rect" x="0" y="0" width={W} height={H} />
          </clipPath>
        </defs>
        <rect x={PAD.l} y={PAD.t} width={IW} height={IH} fill="url(#water)" rx="4" />

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
          {MARKERS.map((m) => {
            const lv = LEVELS[m.level];
            return (
              <g key={m.level}>
                <line x1={x(m.t)} x2={x(m.t)} y1={PAD.t} y2={PAD.t + IH} stroke="#fff" strokeWidth="1.5" strokeDasharray="5 4" />
                <rect x={x(m.t) - 26} y={PAD.t - 24} width="52" height="22" rx="11" fill={lv.color} />
                <text x={x(m.t)} y={PAD.t - 9} textAnchor="middle" fontSize="13" fontWeight="700" fill="#fff">{m.level}</text>
              </g>
            );
          })}
        </g>

        {timeLabels.map((i) => (
          <text key={i} x={x(i)} y={H - 10} textAnchor="middle" fontSize="12" fill="#5b7686">{String(KST_HOURS(i)).padStart(2, '0')}시</text>
        ))}
      </svg>
      <figcaption>
        표층 수온이 {t0}℃에서 {tAlert}℃로 오르자 어군이 아래층으로 내려가고, 단계가 경고에서 위험으로 올라갑니다. 시뮬레이션 데이터입니다.
      </figcaption>
    </figure>
  );
}
