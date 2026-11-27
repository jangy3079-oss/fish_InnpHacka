// 시나리오 재생. 실제 API(/demo/scenario/{name}?t=)가 붙기 전까지 같은 모양의 응답을 만든다.
// scenarios.json은 목업이거나 데이터 담당이 내보낸 파일(`python -m pipeline.frontend`)이며 형식은 같다.
//   {시나리오: {label, meta?, frames: [{t, ts, level, stress_score, ...}], series: {지표: [{ts, value}]}}}
// 시나리오마다 길이와 프레임 간격이 달라도, 값이 null인 프레임이 있어도 동작해야 한다.
import scenarios from './scenarios.json';
import baseRecommendation from './recommendation.json';
import firstAid from './recommendation-disease.json';
import baseStressMap from './stress-map.json';
import { LEVELS, LEVEL_RANK } from '../constants.js';

const ids = Object.keys(scenarios);
export const SCENARIOS = ids.map((id) => ({ id, label: scenarios[id].label ?? id }));

const DEFAULT_ID = scenarios.heatwave_typhoon ? 'heatwave_typhoon' : ids[0];
const scenarioById = (id) => scenarios[id] ?? scenarios[DEFAULT_ID];
const scenarioOf = (demo) => scenarioById(demo?.scenario);

// 시나리오마다 슬라이더 끝이 다를 수 있다.
export const maxTOf = (id) => scenarioById(id).frames.length - 1;

const clampIn = (sc, t) => Math.max(0, Math.min(sc.frames.length - 1, Math.round(t)));

// 처음 보여줄 시점: meta.default_t가 있으면 그것, 없으면 처음 '경고' 이상이 되는 프레임, 그것도 없으면 마지막 프레임.
export function defaultTOf(id) {
  const sc = scenarioById(id);
  if (Number.isInteger(sc.meta?.default_t)) return clampIn(sc, sc.meta.default_t);
  const first = sc.frames.findIndex((f) => (LEVEL_RANK[f.level] ?? -1) >= LEVEL_RANK['경고']);
  return first >= 0 ? first : sc.frames.length - 1;
}

export const DEFAULT_DEMO = { scenario: DEFAULT_ID, t: defaultTOf(DEFAULT_ID) };

function frameAt(demo) {
  const sc = scenarioOf(demo);
  const idx = clampIn(sc, demo?.t ?? DEFAULT_DEMO.t);
  const f = sc.frames[idx];
  const warm = sc.mock_warmup_frames ?? 0; // 목업 전용: 시작 직후를 기준선 학습 중으로 보여준다
  if (idx >= warm) return f;
  return { ...f, baseline_status: 'warming_up', baseline_windows: 120 + 24 * idx, stress_score: null, level: '정상', cause: [] };
}

// 현재 프레임의 시각(ISO). 화면의 시각 표시에 쓴다.
export const frameTime = (demo) => frameAt(demo).ts;

// 서버의 t는 시나리오 시작 후 경과 시간(시간 단위)이다. 프레임에 scenario_t_h가 있으면 그것을, 없으면 첫 프레임과의 시각 차이를 쓴다.
export function hoursAt(demo) {
  const sc = scenarioOf(demo);
  const f = sc.frames[clampIn(sc, demo?.t ?? DEFAULT_DEMO.t)];
  if (typeof f.scenario_t_h === 'number') return Math.round(f.scenario_t_h * 10000) / 10000;
  const hours = (new Date(f.ts) - new Date(sc.frames[0].ts)) / 3600000;
  return Math.round(hours * 10000) / 10000;
}

export function statusAt(cageId, demo) {
  const { t, ...status } = structuredClone(frameAt(demo)); // t는 API 응답에 없는 프레임 번호
  return { ...status, cage_id: Number(cageId) };
}

export function timeseriesAt(cageId, metric, demo) {
  const sc = scenarioOf(demo);
  const points = sc.series[metric] ?? [];
  return { cage_id: Number(cageId), metric, points: points.slice(0, clampIn(sc, demo?.t ?? DEFAULT_DEMO.t) + 1) };
}

export function recommendationAt(cageId, demo) {
  const f = frameAt(demo);
  const base = { id: 1, cage_id: Number(cageId), ts: f.ts };
  if (!f.level) return { ...base, kind: 'none', recommended_option: null, options: [] };
  if (f.level === '정상') {
    // R7(섭식 저하)은 정상 단계에서도 사료 관리 권고를 낸다. 손해 최솟값(keep)과 권고(reduce_feed)가 다르다. 금액은 목업 가정값이다.
    if (f.cause?.[0]?.rule_id !== 'R7') return { ...base, kind: 'none', recommended_option: null, options: [] };
    return {
      ...base,
      kind: 'profit',
      recommended_option: 'reduce_feed',
      details: { lowest_loss_option: 'keep', recommendation_basis: 'R7_feeding_management' },
      options: [
        { id: 'keep', label: '그대로 유지', available: true, estimated_loss_krw: 80000, reason: '먹이 반응이 낮은 상태가 이어질 때의 손해를 가정한 값입니다' },
        { id: 'reduce_feed', label: '사료 줄이기', available: true, estimated_loss_krw: 150000, reason: '사료 투입량을 줄이고 먹는 상태를 살핍니다' },
      ],
    };
  }

  const top = f.cause?.[0]?.rule_id;
  if (top === 'R5' || top === 'R8') {
    return { ...base, id: 2, kind: 'first_aid', recommended_option: 'diagnosis', first_aid: firstAid.first_aid, options: [] };
  }

  const options = structuredClone(baseRecommendation.options);
  let recommended = baseRecommendation.recommended_option;
  if (f.level === '위험') {
    // 위험 단계에서는 긴급방류 요건이 충족된다고 가정한다.
    const rel = options.find((o) => o.id === 'release');
    Object.assign(rel, {
      available: true,
      estimated_loss_krw: 4200000,
      reason: '위험 단계이고 요건을 채웠습니다. 방류 신청 기한 안에 결정해 주세요',
      calc: { formula: '방류 마릿수 × 평균 무게 × kg당 단가 × 보상 제외 비율', inputs: { 방류_마릿수: '2,800마리', 평균_무게: '0.2kg', 'kg당 단가': '15,000원' } },
    });
    recommended = 'wintering';
  }
  return { ...base, kind: 'profit', recommended_option: recommended, options };
}

export function stressMapAt(demo) {
  const f = frameAt(demo);
  const map = structuredClone(baseStressMap);
  map.ts = f.ts;
  // 단계나 지수가 아직 없는(null) 구간은 '정상', 0으로 보여준다.
  Object.assign(map.areas[0], { level: f.level ?? '정상', stress_score: f.stress_score ?? 0 });
  return map;
}

// 단계가 바뀐 시점마다 알림 1건. 실제 문장은 LLM(#34)이 만든다. 단계가 아직 없는(null) 프레임은 건너뛴다.
export function notificationsAt(cageId, demo) {
  const sc = scenarioOf(demo);
  const frames = sc.frames.slice(0, clampIn(sc, demo?.t ?? DEFAULT_DEMO.t) + 1);
  const items = [];
  let prev = null;
  frames.forEach((f) => {
    if (!f.level || !LEVELS[f.level] || f.level === prev) return;
    const cause = f.cause?.[0] ? ` 의심 원인은 ${f.cause[0].name}입니다.` : '';
    items.push({ id: items.length + 1, ts: f.ts, level: f.level, text: `${cageId}번 가두리가 '${f.level}' 단계입니다.${cause} ${LEVELS[f.level].message}.` });
    prev = f.level;
  });
  return { cage_id: Number(cageId), items: items.reverse() };
}

// 해역별 방류 예상 마릿수. 단계가 높을수록 가두리당 방류 마릿수가 커진다고 가정한 목업이다.
const PER_CAGE = { 정상: 0, 주의: 500, 경고: 2000, 위험: 10000 };
export function releaseDemandAt(areaId, demo) {
  const map = stressMapAt(demo);
  const a = map.areas.find((x) => x.area_id === Number(areaId)) ?? map.areas[0];
  return { area_id: a.area_id, ts: map.ts, estimated_fish_count: (PER_CAGE[a.level] ?? 0) * a.cage_count, cage_count: a.cage_count };
}
