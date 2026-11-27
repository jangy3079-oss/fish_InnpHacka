import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { createServer } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

process.env.VITE_USE_MOCK = 'false';
const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });
after(() => server.close());
const { default: Deadlines } = await server.ssrLoadModule('/src/components/RecommendationDeadlines.jsx');
const rec = { deadlines: [
  { kind: 'next_assessment', label: '다음 판단 시점', at: '2026-11-27T13:15:00Z', source_status: 'assumed', applicable: true },
  { kind: 'harvest', label: '출하 가능 기한', at: null, source_status: 'pending', applicable: false },
] };

test('renders Korea time, assumption, pending and unavailable states', () => {
  const html = renderToStaticMarkup(React.createElement(Deadlines, { rec }));
  assert.match(html, /22:15/);
  assert.match(html, /가정 기준/);
  assert.match(html, /확인 필요/);
  assert.match(html, /현재 선택 불가/);
  assert.match(html, /datetime="2026-11-27T13:15:00Z"/i);
});

test('renders legacy missing deadlines and elapsed time, escapes policy text', () => {
  assert.match(renderToStaticMarkup(React.createElement(Deadlines, { rec: {} })), /기한 정보가 없습니다/);
  const r = { deadlines: [{ ...rec.deadlines[0], expired: true, source: '<script>bad</script>' }] };
  const html = renderToStaticMarkup(React.createElement(Deadlines, { rec: r }));
  assert.match(html, /시각 지남/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;/);
});

test('both economic and first aid cards include the deadline display', async () => {
  const { ProfitCompare, FirstAidCard } = await server.ssrLoadModule('/src/components/ProfitCards.jsx');
  assert.match(renderToStaticMarkup(React.createElement(ProfitCompare, { rec: { ...rec, options: [] } })), /다음 판단 시점/);
  assert.match(renderToStaticMarkup(React.createElement(FirstAidCard, { rec: { ...rec, first_aid: [], simulated: true } })), /다음 판단 시점/);
});

test('real API adapter retains deadlines and accepts reviewed first aid objects', async () => {
  const saved = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ ...rec, diagnosis_request_required: true,
    first_aid: { actions: [{ text: '확인된 공통 조치' }] }, options: [] }) });
  try {
    const { api } = await server.ssrLoadModule('/src/api/client.js');
    const result = await api.getRecommendation(3);
    assert.equal(result.kind, 'first_aid');
    assert.deepEqual(result.first_aid, ['확인된 공통 조치']);
    assert.deepEqual(result.deadlines, rec.deadlines);
  } finally { globalThis.fetch = saved; }
});
