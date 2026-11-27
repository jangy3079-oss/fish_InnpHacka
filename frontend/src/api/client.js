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
  const firstAid = (r.first_aid ?? []).map((x) => (typeof x === 'string' ? x : x?.text)).filter(Boolean);
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

const qs = (params) => {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null));
  const s = q.toString();
  return s ? `?${s}` : '';
};

export const api = {
  // demo: {scenario, t}. 시연 중이면 POST /demo/scenario/{name}?t= 로 해당 시각의 상태를 받는다.
  getStatus: (cageId, demo) => {
    if (USE_MOCK) return delay(statusAt(cageId, demo));
    if (!demo) return http(`/cages/${cageId}/status`);
    // 서버의 t는 시나리오 시작 후 경과 시간(시간 단위)이다. 프레임 번호가 아니다.
    return http(`/demo/scenario/${demo.scenario}?t=${hoursAt(demo)}`, { method: 'POST' }).then((r) => ({ ...r.status, cage_id: Number(cageId) }));
  },

  getTimeseries: (cageId, metric, { from, to, demo } = {}) =>
    USE_MOCK
      ? delay(timeseriesAt(cageId, metric, demo))
      : http(`/cages/${cageId}/timeseries${qs({ metric, from, to })}`),

  // 에코그램은 이미지(SVG)이므로 URL을 반환한다.
  getEchogramUrl: (cageId, { from, to } = {}) =>
    USE_MOCK ? echogramMock : `${BASE}/cages/${cageId}/echogram${qs({ from, to })}`,

  // 추천이 없는 회차(정상 등)는 404, 금전 정보 권한이 없으면 403이다. 둘 다 화면 오류가 아니라 '추천 없음'으로 받는다.
  getRecommendation: (cageId, demo) =>
    USE_MOCK
      ? delay(recommendationAt(cageId, demo))
      : http(`/cages/${cageId}/recommendation`)
          .then(normalizeRecommendation)
          .catch((e) => {
            if (e.status === 404) return NO_RECOMMENDATION;
            if (e.status === 403) return { ...NO_RECOMMENDATION, forbidden: true };
            throw e;
          }),

  requestDiagnosis: (cageId, note = '') =>
    USE_MOCK
      ? delay({ mock: true, persisted: false })
      : http(`/cages/${cageId}/diagnosis-request`, {
          method: 'POST',
          body: JSON.stringify({ note }),
        }),

  chooseOption: (recommendationId, optionId) =>
    USE_MOCK
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
