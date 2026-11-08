/* Exercise Shelly widget states inside Telegram's actual QuickJS runtime. */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runWidget } from './sandbox-node.ts';

const source = process.env.SHELLY_WIDGET_HTML;
assert.ok(source, 'SHELLY_WIDGET_HTML is required');
const html = readFileSync(source, 'utf8');
const recent = Math.floor(Date.now() / 1000) - 5;

function state(
  multi: boolean,
  override: Record<string, unknown> = {},
): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    schema: 1, l: 'en', s: 'online', v: 224.4, f: 50, t: recent,
  };
  if (multi) {
    payload.p = {
      L2: { s: 'online', v: 223.3, f: 50, t: recent },
      L3: { s: 'offline' },
    };
  }
  return { ...payload, ...override };
}

interface Visible {
  voltage: string;
  l2: string;
  l3: string;
  hidden: boolean;
  label: string;
}

function decodeProbe(raw: string | undefined): Visible {
  assert.ok(raw, 'Missing widget DOM probe');
  let parsed: unknown = JSON.parse(raw);
  if (typeof parsed === 'string') parsed = JSON.parse(parsed);
  assert.ok(parsed && typeof parsed === 'object', 'Widget DOM probe is empty');
  return parsed as Visible;
}

async function verify(
  label: string,
  data: Record<string, unknown>,
  expected: Partial<Visible>,
): Promise<void> {
  const result = await runWidget(html, {
    data: { publicData: JSON.stringify(data) },
    probes: [
      "JSON.stringify({voltage:document.getElementById('voltage').textContent,l2:document.getElementById('phaseL2').textContent,l3:document.getElementById('phaseL3').textContent,hidden:document.getElementById('extraPhases').hidden,label:document.getElementById('voltageLabel').textContent})",
    ],
    timerRounds: 0,
  });
  assert.equal(result.stopped, undefined, label + ': runtime stopped');
  assert.ok(result.watchesPublicData, label + ': not watching state');
  const visible = decodeProbe(result.probes.at(-1));
  for (const [key, value] of Object.entries(expected)) {
    assert.deepEqual(
      visible[key as keyof Visible], value,
      label + ': incorrect ' + key + ': ' + JSON.stringify(visible),
    );
  }
  console.log('PASS ' + label + ': ' + JSON.stringify(visible));
}

await verify('single-phase', state(false), {
  voltage: '224.4', hidden: true, label: 'Voltage',
});
await verify('mixed-secondary', state(true), {
  voltage: '224.4', l2: '223.3 V', l3: '—', hidden: false,
});
await verify('stale-secondary', state(true, {
  p: {
    L2: { s: 'online', t: recent - 360, v: 219.8 },
    L3: { s: 'online', t: recent, v: 226.4 },
  },
}), {
  voltage: '224.4', l2: '—', l3: '226.4 V', hidden: false,
});
await verify('offline-primary', state(true, {
  s: 'offline', p: {
    L2: { s: 'online', t: recent, v: 222.1 },
    L3: { s: 'offline' },
  },
}), {
  voltage: '—', l2: '222.1 V', l3: '—', hidden: false,
});
console.log('PASS Telegram QuickJS three-phase DOM scenarios');
