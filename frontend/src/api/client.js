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

  getRecommendation: (cageId, demo) =>
    USE_MOCK ? delay(recommendationAt(cageId, demo)) : http(`/cages/${cageId}/recommendation`),

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
          body: JSON.stringify({ option_id: optionId }),
        }),

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
