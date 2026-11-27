import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { build } from '../node_modules/esbuild/lib/main.js';

// Compile the real Vite client with its existing build dependency, no new package.
const root = fileURLToPath(new URL('..', import.meta.url));
const bundled = await build({ entryPoints: [resolve(root, 'src/api/client.js')], bundle: true,
  write: false, format: 'esm', platform: 'node', loader: { '.svg': 'text' },
  define: { 'import.meta.env': JSON.stringify({ VITE_USE_MOCK: 'false', VITE_API_BASE: 'http://localhost' }) },
  plugins: [{ name: 'raw-assets', setup(b) {
    b.onResolve({ filter: /\?raw$/ }, (a) => ({ path: resolve(a.resolveDir, a.path.slice(0, -4)), namespace: 'raw' }));
    b.onLoad({ filter: /.*/, namespace: 'raw' }, async (a) => ({ contents: await readFile(a.path, 'utf8'), loader: 'text' }));
  } }],
});
const { api } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString('base64')}`);

test('diagnosis lookup is GET only; only missing receipt is an empty result', async () => {
  const original = globalThis.fetch;
  const calls = [];
  try {
    globalThis.fetch = async (url, options) => {
      calls.push([url, options.method ?? 'GET']);
      return new Response(JSON.stringify({ detail: { code: 'diagnosis_request_not_found' } }), { status: 404 });
    };
    assert.equal(await api.getDiagnosisRequest(3), null);
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: { code: 'cage_not_found' } }), { status: 404 });
    await assert.rejects(api.getDiagnosisRequest(3), (e) => e.code === 'cage_not_found');
    globalThis.fetch = async () => new Response('{}', { status: 403 });
    await assert.rejects(api.getDiagnosisRequest(3), (e) => e.status === 403);
    assert.deepEqual(calls, [['http://localhost/cages/3/diagnosis-request', 'GET']]);
  } finally { globalThis.fetch = original; }
});

test('uncertain diagnosis POST retains UUID and successful retry clears it', async () => {
  const original = globalThis.fetch;
  const storage = globalThis.sessionStorage;
  const values = new Map();
  const bodies = [];
  globalThis.sessionStorage = { getItem: (k) => values.get(k) ?? null,
    setItem: (k, v) => values.set(k, v), removeItem: (k) => values.delete(k) };
  try {
    globalThis.fetch = async (url, options) => {
      assert.equal(options.method, 'POST');
      bodies.push(JSON.parse(options.body));
      return new Response('{}', { status: bodies.length === 1 ? 503 : 201 });
    };
    await assert.rejects(api.requestDiagnosis(3), (e) => e.status === 503);
    assert.equal(values.size, 1);
    await api.requestDiagnosis(3);
    assert.deepEqual(bodies[0], bodies[1]);
    assert.deepEqual(Object.keys(bodies[0]), ['request_key']);
    assert.equal(values.size, 0);
  } finally { globalThis.fetch = original; globalThis.sessionStorage = storage; }
});
