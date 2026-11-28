import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import { createServer } from 'vite';

process.env.VITE_USE_MOCK = 'true';
const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });
after(() => server.close());
const { stressMapAt, releaseDemandAt } = await server.ssrLoadModule('/src/mocks/demo.js');
const { default: scenarios } = await server.ssrLoadModule('/src/mocks/scenarios.json');
const RULES = ['R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7', 'R8'];

test('cause distribution uses only cause rules and never exceeds alerted cages', () => {
  for (const [scenario, sc] of Object.entries(scenarios)) {
    sc.frames.forEach((_, t) => {
      const map = stressMapAt({ scenario, t });
      const names = map.cause_distribution.map((c) => c.name);
      assert.ok(!names.includes('탁도'), `${scenario} t=${t}`);
      map.cause_distribution.forEach((c) => assert.ok(RULES.includes(c.rule_id), `${scenario} t=${t} ${c.rule_id}`));
      const alerted = map.areas.filter((a) => ['주의', '경고', '위험'].includes(a.level)).reduce((s, a) => s + a.cage_count, 0);
      const total = map.cause_distribution.reduce((s, c) => s + c.count, 0);
      assert.ok(total <= alerted, `${scenario} t=${t}: ${total} > ${alerted}`);
    });
  }
});

test('disease and parasite suspicion follow the scenario and release nothing in area A', () => {
  for (const [scenario, rule] of [['disease_suspect', 'R5'], ['parasite_suspect', 'R8']]) {
    const t = scenarios[scenario].frames.findIndex((f) => f.cause?.some((c) => c.rule_id === rule) && f.level !== '정상');
    assert.ok(t >= 0, scenario);
    assert.equal(stressMapAt({ scenario, t }).cause_distribution.find((c) => c.rule_id === rule)?.count, 1);
    assert.equal(releaseDemandAt(1, { scenario, t }).estimated_fish_count, 0);
  }
});
