import { useEffect, useRef } from 'react';
import * as maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
import { LEVELS } from '../constants.js';

// Vite에서는 워커 파일 경로를 직접 알려줘야 한다.
maplibregl.setWorkerUrl(workerUrl);

// 해역 폴리곤을 경보 단계 색으로 칠한다. 타일 서버 없이 동작하도록 빈 배경 스타일을 쓴다.
export default function AreaMap({ shapes, areas, selectedId, onSelect }) {
  const el = useRef(null);
  const map = useRef(null);

  const data = {
    ...shapes,
    features: shapes.features.map((f) => {
      const a = areas.find((x) => x.area_id === f.properties.area_id);
      return { ...f, properties: { ...f.properties, level: a?.level ?? '정상', name: a?.name ?? '' } };
    }),
  };

  useEffect(() => {
    const colors = ['match', ['get', 'level']];
    Object.entries(LEVELS).forEach(([k, v]) => colors.push(k, v.color));
    colors.push('#9aa6b3');

    const m = new maplibregl.Map({
      container: el.current,
      style: { version: 8, sources: {}, layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#dbe8f3' } }] },
      center: [128.47, 34.78],
      zoom: 9.8,
      attributionControl: false,
    });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    m.on('load', () => {
      m.addSource('areas', { type: 'geojson', data });
      m.addLayer({ id: 'fill', type: 'fill', source: 'areas', paint: { 'fill-color': colors, 'fill-opacity': 0.7 } });
      m.addLayer({
        id: 'line',
        type: 'line',
        source: 'areas',
        paint: { 'line-color': '#13243b', 'line-width': ['case', ['==', ['get', 'area_id'], -1], 4, 1.5] },
      });
      m.on('click', 'fill', (e) => onSelect?.(e.features[0].properties.area_id));
      m.on('mouseenter', 'fill', () => (m.getCanvas().style.cursor = 'pointer'));
      m.on('mouseleave', 'fill', () => (m.getCanvas().style.cursor = ''));
    });
    map.current = m;
    return () => m.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 시연 시각이 바뀌면 해역 색을 갱신한다.
  useEffect(() => {
    map.current?.getSource('areas')?.setData(data);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [areas]);

  useEffect(() => {
    const m = map.current;
    if (m?.getLayer('line')) m.setPaintProperty('line', 'line-width', ['case', ['==', ['get', 'area_id'], selectedId ?? -1], 4, 1.5]);
  }, [selectedId]);

  return <div ref={el} className="area-map" role="img" aria-label="해역별 스트레스 단계 지도" />;
}
