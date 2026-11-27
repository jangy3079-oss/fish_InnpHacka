// 시나리오 재생 목업. 실제 API(/demo/scenario/{name}?t=)가 붙기 전까지 같은 모양의 응답을 만든다.
import scenarios from './scenarios.json';
import baseRecommendation from './recommendation.json';
import firstAid from './recommendation-disease.json';
import baseStressMap from './stress-map.json';
import { LEVELS } from '../constants.js';

export const SCENARIOS = Object.entries(scenarios).map(([id, s]) => ({ id, label: s.label }));
export const MAX_T = scenarios.heatwave_typhoon.frames.length - 1;
export const DEFAULT_DEMO = { scenario: 'heatwave_typhoon', t: 23 };

const clampT = (t) => Math.max(0, Math.min(MAX_T, Math.round(t)));
const scenarioOf = (demo) => scenarios[demo?.scenario] ?? scenarios[DEFAULT_DEMO.scenario];
const frameAt = (demo) => scenarioOf(demo).frames[clampT(demo?.t ?? DEFAULT_DEMO.t)];

export function statusAt(cageId, demo) {
  const { t, ...status } = structuredClone(frameAt(demo)); // t는 API 응답에 없는 목업 전용 값
  return { cage_id: Number(cageId), ...status };
}

export function timeseriesAt(cageId, metric, demo) {
  const points = scenarioOf(demo).series[metric] ?? [];
  return { cage_id: Number(cageId), metric, points: points.slice(0, clampT(demo?.t ?? DEFAULT_DEMO.t) + 1) };
}

export function recommendationAt(cageId, demo) {
  const f = frameAt(demo);
  const base = { id: 1, cage_id: Number(cageId), ts: f.ts };
  if (f.level === '정상') return { ...base, kind: 'none', recommended_option: null, options: [] };

  const top = f.cause[0]?.rule_id;
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
  Object.assign(map.areas[0], { level: f.level, stress_score: f.stress_score });
  return map;
}

// 단계가 바뀐 시점마다 알림 1건. 실제 문장은 LLM(#34)이 만든다.
export function notificationsAt(cageId, demo) {
  const frames = scenarioOf(demo).frames.slice(0, clampT(demo?.t ?? DEFAULT_DEMO.t) + 1);
  const items = [];
  let prev = null;
  frames.forEach((f) => {
    if (f.level !== prev) {
      const cause = f.cause[0] ? ` 의심 원인은 ${f.cause[0].name}입니다.` : '';
      items.push({ id: items.length + 1, ts: f.ts, level: f.level, text: `${cageId}번 가두리가 '${f.level}' 단계입니다.${cause} ${LEVELS[f.level].message}.` });
      prev = f.level;
    }
  });
  return { cage_id: Number(cageId), items: items.reverse() };
}
