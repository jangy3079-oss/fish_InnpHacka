import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { createServer } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

process.env.VITE_USE_MOCK = 'false';
const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });
after(() => server.close());
const mod = await server.ssrLoadModule('/src/components/EvidenceSummary.jsx');
const { default: EvidenceSummary, evidenceRows } = mod;

const ev = {
  surface_ratio_x: 1.0, mid_layer_ratio_x: 1.3, lower_layers_ratio_x: 2.4,
  activity_change: -0.15, feeding_change: -0.46, cohesion_change: 0.1,
  still_ratio_change: 0.6, rubbing_rate_x: 3.2, temp_surface: 24.75, do: 6.57,
};

test('hides values within usual range (ratio 0.7~1.3, change ±15%)', () => {
  const keys = evidenceRows(ev).behavior.map((r) => r.key);
  assert.deepEqual(keys, ['lower_layers_ratio_x', 'feeding_change', 'still_ratio_change', 'rubbing_rate_x']);
});

test('disease puts sinking and still first, parasite puts rubbing first', () => {
  assert.deepEqual(evidenceRows(ev, 'R5').behavior.map((r) => r.key).slice(0, 2), ['lower_layers_ratio_x', 'still_ratio_change']);
  assert.equal(evidenceRows(ev, 'R8').behavior[0].key, 'rubbing_rate_x');
});

test('renders labels, DO and a note when everything is usual', () => {
  const html = renderToStaticMarkup(React.createElement(EvidenceSummary, { evidence: ev, ruleId: 'R8' }));
  assert.match(html, /몸을 비비는 횟수.*평소의 3\.2배/);
  assert.match(html, /용존산소.*6\.6 mg\/L/);
  assert.doesNotMatch(html, /수면 쪽/);
  const calm = renderToStaticMarkup(React.createElement(EvidenceSummary, { evidence: { activity_change: 0.02, do: 7 } }));
  assert.match(calm, /평소와 비슷합니다/);
  assert.equal(renderToStaticMarkup(React.createElement(EvidenceSummary, { evidence: {} })), '');
});
