// 모든 API 호출은 이 파일에서만 한다.
// VITE_USE_MOCK=false 로 바꾸면 목업 대신 실제 API(VITE_API_BASE)를 호출한다.
import status from '../mocks/status.json';
import timeseries from '../mocks/timeseries.json';
import recommendation from '../mocks/recommendation.json';
import recommendationDisease from '../mocks/recommendation-disease.json';
import stressMap from '../mocks/stress-map.json';
<<<<<<< HEAD
import notifications from '../mocks/notifications.json';
=======
import areasGeo from '../mocks/areas.geojson?raw';
>>>>>>> origin/main
import releaseDemand from '../mocks/release-demand.json';
import echogramMock from '../mocks/echogram.svg';

const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false';
const BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000';
const loadedAt = Date.now();
const delay = (v) => new Promise((r) => setTimeout(() => r(structuredClone(v)), 150));

async function http(path, options) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${path}`);
  return res.json();
}

const qs = (params) => {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null));
  const s = q.toString();
  return s ? `?${s}` : '';
};

export const api = {
  // 목업에서는 ?demo=rise 로 페이지를 연 지 5초 뒤부터 단계가 '주의'→'경고'로 오르는 상황을 볼 수 있다.
  getStatus: (cageId) => {
    if (!USE_MOCK) return http(`/cages/${cageId}/status`);
    const rising = new URLSearchParams(window.location.search).get('demo') === 'rise';
    const base = rising && Date.now() - loadedAt < 5000 ? { ...status, level: '주의', stress_score: 41 } : status;
    return delay({ ...base, cage_id: Number(cageId) });
  },

  getTimeseries: (cageId, metric, { from, to } = {}) =>
    USE_MOCK
      ? delay({ cage_id: Number(cageId), metric, points: timeseries[metric] ?? [] })
      : http(`/cages/${cageId}/timeseries${qs({ metric, from, to })}`),

  // 에코그램은 이미지(SVG)이므로 URL을 반환한다.
  getEchogramUrl: (cageId, { from, to } = {}) =>
    USE_MOCK ? echogramMock : `${BASE}/cages/${cageId}/echogram${qs({ from, to })}`,

  // 목업에서는 ?scenario=disease 로 질병 의심(1차 조치) 화면을 확인할 수 있다.
  getRecommendation: (cageId) =>
    USE_MOCK
      ? delay({
          ...(new URLSearchParams(window.location.search).get('scenario') === 'disease'
            ? recommendationDisease
            : recommendation),
          cage_id: Number(cageId),
        })
      : http(`/cages/${cageId}/recommendation`),

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
  getNotifications: (cageId) =>
    USE_MOCK
      ? delay({ ...notifications, cage_id: Number(cageId) })
      : http(`/cages/${cageId}/notifications`),

  getStressMap: () => (USE_MOCK ? delay(stressMap) : http('/areas/stress-map')),

  // 해역 경계(GeoJSON). 계약상 지도 응답 형식이 미정이라 프론트는 area_id로 stress-map과 이어 붙인다.
  getAreaShapes: () => (USE_MOCK ? delay(JSON.parse(areasGeo)) : http('/areas/shapes')),

  getReleaseDemand: (areaId) =>
    USE_MOCK
      ? delay({ ...releaseDemand, area_id: Number(areaId) })
      : http(`/areas/${areaId}/release-demand`),
};
