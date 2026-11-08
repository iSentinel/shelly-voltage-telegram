/* Verify repeated status updates do not leave stale Telegram DOM nodes. */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runWidget } from './sandbox-node.ts';

const source = process.env.SHELLY_WIDGET_HTML;
assert.ok(source, 'SHELLY_WIDGET_HTML is required');
const html = readFileSync(source, 'utf8');
const now = Math.floor(Date.now() / 1000);
const fresh = {
  schema: 1, l: 'en', s: 'online', v: 220.0, f: 50,
  t: now - 3, d: '2026-11-08', g: '10.1',
  src: 'community', b: 'OX?O'.repeat(12), u: now - 20,
  e: now + 3600,
  p: {
    L2: { s: 'online', t: now - 3, v: 222.1, f: 50 },
    L3: { s: 'offline' },
  },
};
const offline = { schema: 1, l: 'ru', s: 'offline' };
const expired = {
  ...fresh, l: 'uk', u: now - 8000,
  p: { L2: { s: 'stale' }, L3: { s: 'offline' } },
};
const payloads = [fresh, offline, expired];
const script: (
  { data: { publicData: string } } | { probe: string }
)[] = [];

for (let i = 0; i < 33; i++) {
  const state = payloads[i % payloads.length];
  const serialized = JSON.stringify(state);
  assert.ok(Buffer.byteLength(serialized) < 1024);
  script.push({ data: { publicData: serialized } });
}
script.push({ data: { publicData: JSON.stringify(fresh) } });
script.push({
  probe: "JSON.stringify({voltage:document.getElementById('voltage').textContent,phaseL2:document.getElementById('phaseL2').textContent,cells:document.getElementById('timeline').children.length,groupHidden:document.getElementById('group').hidden})",
});

const result = await runWidget(html, {
  data: { publicData: JSON.stringify(fresh) },
  script,
  timerRounds: 0,
});
assert.equal(result.stopped, undefined, 'QuickJS stopped after many updates');
assert.ok(result.watchesPublicData, 'Widget did not register updates');
assert.equal(result.requests.length, 0, 'Widget attempted a network call');
assert.ok(result.nodes < 2000, 'Widget exceeded the 2000 DOM node limit');
assert.equal(result.probes.length, 1, 'Missing final DOM inspection');

let actual: unknown = JSON.parse(result.probes[0]);
if (typeof actual === 'string') actual = JSON.parse(actual);
assert.ok(actual && typeof actual === 'object', 'Invalid final probe');
const final = actual as {
  voltage: string; phaseL2: string; cells: number; groupHidden: boolean;
};
assert.deepEqual(final, {
  voltage: '220.0', phaseL2: '222.1 V',
  cells: 48, groupHidden: false,
});
console.log(
  'PASS widget repeated-data stress: 34 updates, ' +
  result.nodes + ' active nodes, ' + result.requests.length + ' requests',
);
