import { useEffect, useRef } from 'react';
import * as maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
import { LEVELS } from '../constants.js';

// Vite에서는 워커 파일 경로를 직접 알려줘야 한다.
maplibregl.setWorkerUrl(workerUrl);

const centroid = (feature) => {
  const ring = feature.geometry.coordinates[0].slice(0, -1); // 마지막 점은 첫 점과 같다
  const sum = ring.reduce((a, [x, y]) => [a[0] + x, a[1] + y], [0, 0]);
  return [sum[0] / ring.length, sum[1] / ring.length];
};

const boundsOf = (shapes) => {
  const b = new maplibregl.LngLatBounds();
  shapes.features.forEach((f) => f.geometry.coordinates[0].forEach((c) => b.extend(c)));
  return b;
};

function popupNode(a) {
  const lv = LEVELS[a.level];
  const box = document.createElement('div');
  box.className = 'pop';
  const name = document.createElement('strong');
  name.textContent = a.name; // 서버 값이므로 textContent로 넣는다
  const level = document.createElement('span');
  level.className = 'pop-level';
  level.style.color = lv?.color ?? '#14212b';
  level.textContent = `${lv?.icon ?? ''} ${a.level ?? '미집계'}`;
  const score = document.createElement('span');
  score.textContent = `스트레스 지수 ${a.stress_score == null ? '미집계' : Math.round(a.stress_score)}`;
  const cages = document.createElement('span');
  cages.textContent = `가두리 ${a.cage_count}곳`;
  const coverage = document.createElement('span');
  coverage.textContent = a.data_status ? `집계 ${a.assessed_cage_count} · 단계 유지 ${a.held_cage_count} · 미집계 ${a.unavailable_cage_count}` : '';
  box.append(name, level, score, cages, coverage);
  return box;
}

// 해역 폴리곤을 경보 단계 색으로 칠한다. 타일 서버와 지도 글꼴 없이 동작하도록 빈 배경 스타일을 쓰고,
// 해역 이름은 HTML 라벨로 올린다.
export default function AreaMap({ shapes, areas, selectedId, onSelect }) {
  const el = useRef(null);
  const map = useRef(null);
  const api = useRef({});
  const areasRef = useRef(areas);
  const selRef = useRef(selectedId);
  const onSelectRef = useRef(onSelect);
  areasRef.current = areas;
  selRef.current = selectedId;
  onSelectRef.current = onSelect;

  const data = {
    ...shapes,
    features: shapes.features.map((f) => {
      const a = areas.find((x) => x.area_id === f.properties.area_id);
      return { ...f, properties: { ...f.properties, level: a?.level ?? '미집계', name: a?.name ?? '' } };
    }),
  };

  useEffect(() => {
    const colors = ['match', ['get', 'level']];
    Object.entries(LEVELS).forEach(([k, v]) => colors.push(k, v.color));
    colors.push('#9aa6b3');

    const featuresById = Object.fromEntries(shapes.features.map((f) => [f.properties.area_id, f]));
    const m = new maplibregl.Map({
      container: el.current,
      style: { version: 8, sources: {}, layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#dbe8f3' } }] },
      bounds: boundsOf(shapes),
      fitBoundsOptions: { padding: 56, maxZoom: 11 },
      attributionControl: false,
      cooperativeGestures: false,
    });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');

    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12, className: 'area-popup' });
    const markers = {};
    let hoverId = null;
    const areaOf = (id) => areasRef.current.find((a) => a.area_id === id);

    // 마우스를 올린 해역이 있으면 그것을, 없으면 선택된 해역의 안내를 보여준다.
    const refreshPopup = () => {
      const id = hoverId ?? selRef.current;
      const a = id != null ? areaOf(id) : null;
      const f = id != null ? featuresById[id] : null;
      if (!a || !f) {
        popup.remove();
        return;
      }
      popup.setLngLat(centroid(f)).setDOMContent(popupNode(a)).addTo(m);
    };

    // 해역 이름 라벨(HTML). 단계 색과 기호를 함께 보여준다.
    const refreshLabels = () => {
      Object.entries(markers).forEach(([id, { el: node, nameEl, levelEl }]) => {
        const a = areaOf(Number(id));
        if (!a) return;
        const lv = LEVELS[a.level];
        nameEl.textContent = a.name;
        levelEl.textContent = `${lv?.icon ?? ''} ${a.level ?? '미집계'}`;
        node.style.borderColor = lv?.color ?? '#9aa6b3';
        levelEl.style.color = lv?.color ?? '#14212b';
        node.classList.toggle('selected', Number(id) === selRef.current);
        node.setAttribute('aria-pressed', String(Number(id) === selRef.current));
      });
    };

    const refreshPaint = () => {
      if (!m.getLayer('fill')) return;
      const sel = selRef.current ?? -1;
      m.setPaintProperty('fill', 'fill-opacity', ['case', ['==', ['get', 'area_id'], sel], 0.92, 0.68]);
      m.setPaintProperty('line', 'line-width', ['case', ['==', ['get', 'area_id'], sel], 4, 1.5]);
    };

    m.on('load', () => {
      m.addSource('areas', { type: 'geojson', data });
      m.addLayer({ id: 'fill', type: 'fill', source: 'areas', paint: { 'fill-color': colors, 'fill-opacity': 0.68 } });
      m.addLayer({ id: 'line', type: 'line', source: 'areas', paint: { 'line-color': '#13243b', 'line-width': 1.5 } });

      shapes.features.forEach((f) => {
        const id = f.properties.area_id;
        const node = document.createElement('button');
        node.type = 'button';
        node.className = 'area-label';
        const nameEl = document.createElement('strong');
        const levelEl = document.createElement('span');
        node.append(nameEl, levelEl);
        node.addEventListener('click', (e) => {
          e.stopPropagation();
          onSelectRef.current?.(selRef.current === id ? null : id);
        });
        markers[id] = { el: node, nameEl, levelEl, marker: new maplibregl.Marker({ element: node }).setLngLat(centroid(f)).addTo(m) };
      });

      m.on('mousemove', 'fill', (e) => {
        hoverId = e.features[0].properties.area_id;
        m.getCanvas().style.cursor = 'pointer';
        refreshPopup();
      });
      m.on('mouseleave', 'fill', () => {
        hoverId = null;
        m.getCanvas().style.cursor = '';
        refreshPopup();
      });
      m.on('click', (e) => {
        const hit = m.queryRenderedFeatures(e.point, { layers: ['fill'] })[0];
        onSelectRef.current?.(hit ? (selRef.current === hit.properties.area_id ? null : hit.properties.area_id) : null);
      });

      refreshLabels();
      refreshPaint();
      refreshPopup();
    });

    map.current = m;
    api.current = { refreshLabels, refreshPaint, refreshPopup };
    return () => {
      Object.values(markers).forEach(({ marker }) => marker.remove());
      popup.remove();
      m.remove();
    };
    // 지도는 한 번만 만들고, 이후 값 변화는 아래 효과들이 반영한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 시연 시각이 바뀌면 해역 색과 라벨, 안내를 갱신한다.
  useEffect(() => {
    map.current?.getSource('areas')?.setData(data);
    api.current.refreshLabels?.();
    api.current.refreshPopup?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [areas]);

  // 표에서 고르든 지도에서 고르든 같은 선택을 반영한다.
  useEffect(() => {
    api.current.refreshPaint?.();
    api.current.refreshLabels?.();
    api.current.refreshPopup?.();
  }, [selectedId]);

  return (
    <div className="area-map-wrap" role="group" aria-label="해역별 스트레스 단계 지도. 같은 내용이 오른쪽 표에도 있습니다.">
      <div ref={el} className="area-map" />
      <ul className="map-legend" aria-label="범례">
        {Object.entries(LEVELS).map(([name, lv]) => (
          <li key={name}>
            <span className="legend-chip" style={{ background: lv.bg, borderColor: lv.color, color: lv.color }} aria-hidden="true">{lv.icon}</span>
            {name}
          </li>
        ))}
      </ul>
    </div>
  );
}
