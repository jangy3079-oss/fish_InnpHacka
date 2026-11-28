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

// 4분 시연 대본(#41)의 장면. 슬라이더 위치는 현재 시연 데이터 기준이다.
export const DEMO_SCENES = [
  { label: '정상', scenario: 'heatwave_typhoon', t: 5 },
  { label: '사료 권고', scenario: 'heatwave_typhoon', t: 12 },
  { label: '고수온 피신', scenario: 'heatwave_typhoon', t: 20 },
  { label: '위험·손익', scenario: 'heatwave_typhoon', t: 26 },
  { label: '태풍', scenario: 'heatwave_typhoon', t: 35 },
  { label: '질병 의심', scenario: 'disease_suspect', t: 34 },
  { label: '기생충 의심', scenario: 'parasite_suspect', t: 39 },
  { label: '탁도 상승', scenario: 'turbidity_event', t: 24 },
];

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

// 다음 판단까지의 간격(분). 단계별 가정값이다(costs.reassessment_minutes_by_level).
const REASSESS_MIN = { 정상: 60, 주의: 30, 경고: 15, 위험: 5 };

// 추천에 기한 목록을 붙인다(docs/recommendation-deadlines.md). 다음 판단은 모든 추천에 있고,
// 방류 신청·출하 기한은 해당 선택지가 있을 때만 넣는다. 확인된 시각이 없으므로 pending, at=null이다(시각을 만들지 않는다).
function withDeadlines(rec, level) {
  const next = new Date(new Date(rec.ts).getTime() + (REASSESS_MIN[level] ?? 60) * 60000).toISOString();
  const deadlines = [
    { kind: 'next_assessment', label: '다음 판단', action_id: null, applicable: true, at: next, source_status: 'assumed', basis: 'relative', source: '시연용 가정', expired: false },
  ];
  const pending = (kind, label, action_id) => {
    const o = rec.options.find((x) => x.id === action_id);
    if (o) deadlines.push({ kind, label, action_id, applicable: !!o.available, at: null, source_status: 'pending', basis: 'unconfirmed', source: null, expired: false });
  };
  pending('release_application', '방류 신청 마감', 'release');
  pending('harvest', '출하 기한', 'harvest');
  const times = deadlines.filter((d) => d.applicable && d.at).map((d) => d.at).sort();
  return { ...rec, deadline: times[0] ?? null, deadline_expired: false, deadlines };
}

// 목업 손익 선택지에 서버의 calculation 모양을 붙인다(결과 금액과 맞는 가정값. 경제 산식이 아니라 표시용 목업이다).
function withCalculation(rec) {
  const STOCK_VALUE = 3000000; // 중간어 1000마리 x 0.3kg x 10,000원
  const options = rec.options.map((o) => {
    if (o.estimated_loss_krw == null || !o.available) return o;
    const cost = ['keep', 'stop_feed', 'reduce_feed'].includes(o.id) ? 0 : Math.round((o.estimated_loss_krw * 0.2) / 1000) * 1000;
    const stock = o.estimated_loss_krw - cost;
    return {
      ...o,
      calculation: {
        hypothetical: false,
        groups: [{ size_group: 'mid', count: 1000, price_per_kg: '10000.0', avg_weight_kg: '0.3', insured: false, price_source: 'config_assumption', weight_source: 'stock_input', stock_value_krw: `${STOCK_VALUE}.00`, loss_rate: (stock / STOCK_VALUE).toFixed(4), loss_krw: `${stock}.000` }],
        stock_loss_krw: `${stock}.000`,
        action_cost_krw: `${cost}.0`,
        insurance_krw: '0',
        subsidy_krw: '0',
        exact_loss_krw: `${o.estimated_loss_krw}.000`,
        insurance_confirmed: false,
      },
    };
  });
  return { ...rec, options, config_revision: '16241d793c95c4230d7639bca7a9c76f68b2675d331ca8fb1be2e501434d15a5' };
}

export function recommendationAt(cageId, demo) {
  const f = frameAt(demo);
  const rec = buildRecommendation(cageId, f);
  return rec.kind === 'none' ? rec : withDeadlines(rec.kind === 'profit' ? withCalculation(rec) : rec, f.level);
}

function buildRecommendation(cageId, f) {
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

// 서버(area_cause_distribution)처럼 표시되는 원인 중 확률이 가장 높은 것을 1순위로 본다.
const topCause = (f) =>
  (f.cause ?? []).filter((c) => c.display !== false).reduce((a, c) => (!a || (c.probability ?? 0) > (a.probability ?? 0) ? c : a), null);
// 해역 전체에 영향을 주는 환경 원인. 나머지(질병·기생충·작업·섭식)는 가두리마다 다르다.
const AREA_WIDE = ['R1', 'R2', 'R3', 'R4'];
const ALERT = ['주의', '경고', '위험'];

export function stressMapAt(demo) {
  const f = frameAt(demo);
  const map = structuredClone(baseStressMap);
  map.ts = f.ts;
  // 단계나 지수가 아직 없는(null) 구간은 '정상', 0으로 보여준다.
  Object.assign(map.areas[0], { level: f.level ?? '정상', stress_score: f.stress_score ?? 0 });
  // 원인 분포: stress-map.json은 다른 해역(B) 몫만 담고, 해역 A 몫은 시연 프레임을 따른다.
  // 환경 원인이면 해역 A 가두리 전체, 개별 원인이면 시연 가두리 1곳으로 센다. 경보 단계가 아니면 세지 않는다.
  const top = topCause(f);
  if (top && ALERT.includes(f.level)) {
    const n = AREA_WIDE.includes(top.rule_id) ? map.areas[0].cage_count : 1;
    const hit = map.cause_distribution.find((c) => c.rule_id === top.rule_id);
    if (hit) hit.count += n;
    else map.cause_distribution.push({ rule_id: top.rule_id, name: top.name, count: n });
    map.cause_distribution.sort((a, b) => a.rule_id.localeCompare(b.rule_id));
  }
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
  // 질병·기생충 의심은 손익 비교가 아니라 1차 조치·진단 흐름이라 방류를 추천하지 않는다(서버도 방류 추천 가두리만 센다).
  const sick = a === map.areas[0] && ['R5', 'R8'].includes(topCause(frameAt(demo))?.rule_id);
  const perCage = sick ? 0 : PER_CAGE[a.level] ?? 0;
  return { area_id: a.area_id, ts: map.ts, estimated_fish_count: perCage * a.cage_count, cage_count: a.cage_count };
}
