// 판단 근거 중 '평소와 달라진 것'만 추려 보여준다. API가 준 값 중 아는 것만 쓰고, 없는 값은 건너뛴다.
// 배수(_x)는 0.7~1.3배, 변화율(_change)은 ±15% 안이면 평소와 비슷하다고 보고 숨긴다.
// 수온·용존산소는 기준선이 없는 현재 환경값이라 늘 보여준다.
export const RATIO_NORMAL = [0.7, 1.3];
export const CHANGE_NORMAL = 0.15;

const ratio = (v) => `평소의 ${v.toFixed(1)}배`;
const change = (v) => {
  const pct = Math.round(Math.abs(v) * 100);
  return v < 0 ? `평소보다 ${pct}% 줄었음` : `평소보다 ${pct}% 늘었음`;
};

const BEHAVIOR = [
  { key: 'surface_ratio_x', label: '수면 쪽에 있는 물고기', kind: 'ratio' },
  { key: 'mid_layer_ratio_x', label: '중층에 있는 물고기', kind: 'ratio' },
  { key: 'lower_layers_ratio_x', label: '아래쪽에 가라앉은 물고기', kind: 'ratio' },
  { key: 'activity_change', label: '움직임', kind: 'change' },
  { key: 'feeding_change', label: '먹이 반응', kind: 'change' },
  { key: 'cohesion_change', label: '모여 있는 정도', kind: 'change' },
  { key: 'still_ratio_change', label: '가만히 있는 물고기', kind: 'change' },
  { key: 'rubbing_rate_x', label: '몸을 비비는 횟수', kind: 'ratio' },
];

const ENVIRONMENT = [
  { key: 'temp_surface', label: '표층 수온', fmt: (v) => `${v.toFixed(1)}℃` },
  { key: 'temp_mid', label: '중층 수온', fmt: (v) => `${v.toFixed(1)}℃` },
  { key: 'do', label: '용존산소', fmt: (v) => `${v.toFixed(1)} mg/L` },
];

// 원인별로 어민이 먼저 봐야 할 값을 위로 올린다.
const PRIORITY = {
  R5: ['lower_layers_ratio_x', 'still_ratio_change'], // 질병: 가라앉음·정지
  R8: ['rubbing_rate_x'], // 기생충: 비비기
};

export function isUsual(kind, v) {
  return kind === 'ratio' ? v >= RATIO_NORMAL[0] && v <= RATIO_NORMAL[1] : Math.abs(v) <= CHANGE_NORMAL;
}

export function evidenceRows(evidence, ruleId) {
  const has = (i) => typeof evidence?.[i.key] === 'number';
  const first = PRIORITY[ruleId] ?? [];
  const rank = (i) => (first.includes(i.key) ? first.indexOf(i.key) : first.length);
  const behavior = BEHAVIOR.filter((i) => has(i) && !isUsual(i.kind, evidence[i.key]))
    .map((i, n) => ({ ...i, n }))
    .sort((a, b) => rank(a) - rank(b) || a.n - b.n)
    .map((i) => ({ key: i.key, label: i.label, text: (i.kind === 'ratio' ? ratio : change)(evidence[i.key]) }));
  const environment = ENVIRONMENT.filter(has).map((i) => ({ key: i.key, label: i.label, text: i.fmt(evidence[i.key]) }));
  const shown = BEHAVIOR.filter(has).length;
  return { behavior, environment, anyBehavior: shown > 0, hidden: shown - behavior.length };
}

export default function EvidenceSummary({ evidence, ruleId }) {
  const { behavior, environment, anyBehavior, hidden } = evidenceRows(evidence, ruleId);
  if (!anyBehavior && !environment.length) return null;
  const rows = [...behavior, ...environment];
  return (
    <section className="card flat">
      <h2>판단 근거 요약</h2>
      {anyBehavior && !behavior.length && <p>물고기 행동은 평소와 비슷합니다.</p>}
      {rows.length > 0 && (
        <dl className="facts">
          {rows.map((i) => (
            <div key={i.key}>
              <dt>{i.label}</dt>
              <dd>{i.text}</dd>
            </div>
          ))}
        </dl>
      )}
      {behavior.length > 0 && hidden > 0 && <p className="hint">평소와 비슷한 값은 숨겼습니다.</p>}
      <p className="hint"><a href="#evidence">그래프로 자세히 보기</a></p>
    </section>
  );
}
