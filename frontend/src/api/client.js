// 모든 API 호출은 이 파일에서만 한다.
// VITE_USE_MOCK=false 로 바꾸면 목업 대신 실제 API(VITE_API_BASE)를 호출한다.
import { hoursAt, notificationsAt, recommendationAt, releaseDemandAt, statusAt, stressMapAt, timeseriesAt } from '../mocks/demo.js';
import areasGeo from '../mocks/areas.geojson?raw';
import echogramMock from '../mocks/echogram.svg';

const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false';
const BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000';
const delay = (v) => new Promise((r) => setTimeout(() => r(structuredClone(v)), 150));

async function http(path, options) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    // 화면에서 상태 코드로 안내 문구를 가를 수 있게 담아 둔다.
    const err = new Error(`API ${res.status}: ${path}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

// 실제 추천 응답을 화면이 읽는 모양으로 맞춘다. kind가 없으면 응답 필드로 판단한다.
function normalizeRecommendation(r) {
  const aid = r.first_aid?.actions ?? r.first_aid ?? [];
  const firstAid = (Array.isArray(aid) ? aid : []).map((x) => (typeof x === 'string' ? x : x?.text)).filter(Boolean);
  let kind = r.kind;
  if (!kind) {
    if (r.diagnosis_request_required) kind = 'first_aid';
    else if (r.options?.length) kind = 'profit';
    else kind = 'none';
  }
  return { ...r, kind, options: r.options ?? [], first_aid: firstAid, recommended_option: r.recommended_option ?? null };
}
const NO_RECOMMENDATION = { id: null, kind: 'none', recommended_option: null, options: [] };

// 선택 요청의 request_key: 한 번 만들면 성공이나 확정 오류가 나올 때까지 같은 값으로 다시 보낸다.
const keyName = (recId, optionId) => `choice-key:${recId}:${optionId}`;
function choiceKey(recId, optionId) {
  const name = keyName(recId, optionId);
  let key = sessionStorage.getItem(name);
  if (!key) {
    key = crypto.randomUUID();
    sessionStorage.setItem(name, key);
  }
  return key;
}

// 시연 응답(POST /demo/scenario)은 한 번의 계산으로 status·recommendation·timeseries를 함께 준다.
// 같은 시각의 요청은 한 번만 보내고, 세 호출이 같은 응답을 나눠 쓴다. 준비 중(503)이면 몇 번 다시 시도한다.
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function withRetry503(fn, tries = 3, waitMs = 3000) {
  for (let i = 0; ; i += 1) {
    try {
      return await fn();
    } catch (e) {
      if (e.status !== 503 || i >= tries) throw e;
      await sleep(waitMs);
    }
  }
}
const demoCache = new Map();
function demoResult(demo) {
  const hours = hoursAt(demo); // 서버의 t는 시나리오 시작 후 경과 시간(시간 단위). 프레임 번호가 아니다.
  const key = `${demo.scenario}:${hours}`;
  if (!demoCache.has(key)) {
    const p = withRetry503(() => http(`/demo/scenario/${demo.scenario}?t=${hours}`, { method: 'POST' }));
    p.catch(() => demoCache.delete(key));
    demoCache.set(key, p);
    if (demoCache.size > 8) demoCache.delete(demoCache.keys().next().value);
  }
  return demoCache.get(key);
}

const qs = (params) => {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null));
  const s = q.toString();
  return s ? `?${s}` : '';
};

export const api = {
  // 시연 입력이 바뀔 때 바로 요청하지 않고 잠시 기다린다(실제 서버는 한 번에 하나씩 계산한다).
  debounceMs: USE_MOCK ? 0 : 300,
  isMock: USE_MOCK, // 목업 모드면 모든 화면이 파일 데이터다

  // demo: {scenario, t}. 시연 중이면 POST /demo/scenario/{name}?t= 응답의 status를 쓴다.
  getStatus: (cageId, demo) => {
    if (USE_MOCK) return delay({ ...statusAt(cageId, demo), simulated: true });
    if (!demo) return http(`/cages/${cageId}/status`);
    return demoResult(demo).then((r) => ({
      ...r.status,
      cage_id: Number(cageId),
      cause_source: r.status?.cause_source ?? r.cause_source,
      simulated: r.simulated !== false,
    }));
  },

  getTimeseries: (cageId, metric, { from, to, demo } = {}) => {
    if (USE_MOCK) return delay(timeseriesAt(cageId, metric, demo));
    // 시연 중에는 같은 응답의 timeseries를 쓴다. DB 경로를 부르지 않는다.
    if (demo) return demoResult(demo).then((r) => ({ cage_id: Number(cageId), metric, points: r.timeseries?.[metric] ?? [] }));
    return http(`/cages/${cageId}/timeseries${qs({ metric, from, to })}`);
  },

  // 에코그램은 이미지(SVG)이므로 URL을 반환한다.
  getEchogramUrl: (cageId, { from, to } = {}) =>
    USE_MOCK ? echogramMock : `${BASE}/cages/${cageId}/echogram${qs({ from, to })}`,

  // 추천이 없는 회차(정상 등)는 404, 금전 정보 권한이 없으면 403이다. 둘 다 화면 오류가 아니라 '추천 없음'으로 받는다.
  getRecommendation: (cageId, demo) => {
    if (USE_MOCK) return delay({ ...recommendationAt(cageId, demo), simulated: true });
    // 시연 응답의 추천은 id=0, selectable=false인 표시 전용이다. recommendation이 null이면 추천 없음이다.
    if (demo) {
      return demoResult(demo).then((r) =>
        r.recommendation
          ? { ...normalizeRecommendation(r.recommendation), cage_id: Number(cageId), simulated: true }
          : { ...NO_RECOMMENDATION, cage_id: Number(cageId), simulated: true, reason: r.recommendation_reason },
      );
    }
    return http(`/cages/${cageId}/recommendation`)
      .then(normalizeRecommendation)
      .catch((e) => {
        if (e.status === 404) return NO_RECOMMENDATION;
        if (e.status === 403) return { ...NO_RECOMMENDATION, forbidden: true };
        throw e;
      });
  },

  requestDiagnosis: (cageId, note = '') =>
    USE_MOCK
      ? delay({ mock: true, persisted: false })
      : http(`/cages/${cageId}/diagnosis-request`, {
          method: 'POST',
          body: JSON.stringify({ note }),
        }),

  // 합성 추천(id=0)은 표시 전용이라 저장 API로 보내지 않는다.
  chooseOption: (recommendationId, optionId) =>
    USE_MOCK || recommendationId === 0
      ? delay({ mock: true, persisted: false })
      : http(`/recommendations/${recommendationId}/choice`, {
          method: 'POST',
          body: JSON.stringify({ option_id: optionId, request_key: choiceKey(recommendationId, optionId) }),
        }).then(
          (r) => {
            sessionStorage.removeItem(keyName(recommendationId, optionId));
            return r;
          },
          (e) => {
            // 서버가 확정적으로 거절한 경우에만 키를 버린다. 타임아웃·네트워크 오류·5xx는 같은 키로 다시 보낸다.
            if ([403, 409, 422].includes(e.status)) sessionStorage.removeItem(keyName(recommendationId, optionId));
            throw e;
          },
        ),

  // 계약 초안에 없는 API. 알림 문장 생성(#34)과 함께 백엔드와 맞춘다.
  getNotifications: (cageId, demo) =>
    USE_MOCK ? delay(notificationsAt(cageId, demo)) : http(`/cages/${cageId}/notifications`),

  // body: {stock: [{size_group: 'fry'|'mid'|'market', count, avg_weight_kg}], insured}
  putStock: (cageId, body) =>
    USE_MOCK
      ? delay({ mock: true, persisted: false })
      : http(`/cages/${cageId}/stock`, { method: 'PUT', body: JSON.stringify(body) }),

  getStressMap: (demo) => (USE_MOCK ? delay(stressMapAt(demo)) : http('/areas/stress-map')),

  // 해역 경계(GeoJSON). 계약상 지도 응답 형식이 미정이라 프론트는 area_id로 stress-map과 이어 붙인다.
  getAreaShapes: () => (USE_MOCK ? delay(JSON.parse(areasGeo)) : http('/areas/shapes')),

  getReleaseDemand: (areaId, demo) =>
    USE_MOCK ? delay(releaseDemandAt(areaId, demo)) : http(`/areas/${areaId}/release-demand`),
};
