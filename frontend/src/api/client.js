// 모든 API 호출은 이 파일에서만 한다.
// VITE_USE_MOCK=false 로 바꾸면 목업 대신 실제 API(VITE_API_BASE)를 호출한다.
import status from '../mocks/status.json';
import timeseries from '../mocks/timeseries.json';
import recommendation from '../mocks/recommendation.json';
import stressMap from '../mocks/stress-map.json';
import releaseDemand from '../mocks/release-demand.json';

const USE_MOCK = import.meta.env.VITE_USE_MOCK !== 'false';
const BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000';
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
  getStatus: (cageId) =>
    USE_MOCK ? delay({ ...status, cage_id: Number(cageId) }) : http(`/cages/${cageId}/status`),

  getTimeseries: (cageId, metric, { from, to } = {}) =>
    USE_MOCK
      ? delay({ ...timeseries, cage_id: Number(cageId), metric })
      : http(`/cages/${cageId}/timeseries${qs({ metric, from, to })}`),

  // 에코그램은 이미지(SVG)이므로 URL을 반환한다.
  getEchogramUrl: (cageId, { from, to } = {}) =>
    `${BASE}/cages/${cageId}/echogram${qs({ from, to })}`,

  getRecommendation: (cageId) =>
    USE_MOCK
      ? delay({ ...recommendation, cage_id: Number(cageId) })
      : http(`/cages/${cageId}/recommendation`),

  chooseOption: (recommendationId, optionId) =>
    USE_MOCK
      ? delay({ mock: true, persisted: false })
      : http(`/recommendations/${recommendationId}/choice`, {
          method: 'POST',
          body: JSON.stringify({ option_id: optionId }),
        }),

  getStressMap: () => (USE_MOCK ? delay(stressMap) : http('/areas/stress-map')),

  getReleaseDemand: (areaId) =>
    USE_MOCK
      ? delay({ ...releaseDemand, area_id: Number(areaId) })
      : http(`/areas/${areaId}/release-demand`),
};
