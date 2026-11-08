/* Validate changes to a single Telegram widget message inside QuickJS. */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runWidget } from './sandbox-node.ts';

const path = process.env.SHELLY_WIDGET_HTML;
assert.ok(path, 'SHELLY_WIDGET_HTML is required');
const html = readFileSync(path, 'utf8');
const timestamp = Math.floor(Date.now() / 1000) - 4;

interface Visible {
  voltage: string;
  phaseL2: string;
  phaseL3: string;
  multiHidden: boolean;
  axisHidden: boolean;
  groupHidden: boolean;
  cells: number;
  state: string;
  label: string;
  source: string;
}

const probe = "JSON.stringify({voltage:document.getElementById('voltage').textContent,phaseL2:document.getElementById('phaseL2').textContent,phaseL3:document.getElementById('phaseL3').textContent,multiHidden:document.getElementById('extraPhases').hidden,axisHidden:document.getElementById('axis').hidden,groupHidden:document.getElementById('group').hidden,cells:document.getElementById('timeline').children.length,state:document.getElementById('state').textContent,label:document.getElementById('voltageLabel').textContent,source:document.getElementById('source').textContent})";

function live(
  voltage: number, language: string, forecast: boolean,
  phases: Record<string, unknown> | null = null,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    schema: 1, l: language, s: 'online', v: voltage, f: 50, t: timestamp,
  };
  if (phases) payload.p = phases;
  if (forecast) {
    payload.src = 'community';
    payload.d = '2026-11-08';
    payload.g = '10.1';
    payload.b = 'OX?O'.repeat(12);
    payload.u = timestamp - 25;
    payload.e = timestamp + 3600;
  }
  return payload;
}

function stringify(payload: Record<string, unknown>): string {
  const encoded = JSON.stringify(payload);
  assert.ok(Buffer.byteLength(encoded) <= 1024);
  return encoded;
}

function parse(raw: string): Visible {
  let value: unknown = JSON.parse(raw);
  if (typeof value === 'string') value = JSON.parse(value);
  assert.ok(value && typeof value === 'object', 'Probe was empty');
  return value as Visible;
}

const initial = live(223.5, 'en', true, {
  L2: { s: 'online', v: 224.1, f: 50, t: timestamp },
  L3: { s: 'online', v: 222.2, f: 50, t: timestamp },
});
const offline = { schema: 1, l: 'ru', s: 'offline' };
const recovered = live(225.2, 'uk', true, {
  L2: { s: 'stale' },
  L3: { s: 'online', v: 223.9, f: 50, t: timestamp },
});
const expired = { ...recovered, u: timestamp - 8000 };
const onePhase = live(221.8, 'en', false);
const steps = [offline, recovered, expired, onePhase];

const script: (
  { data: { publicData: string } } | { probe: string }
)[] = [{ probe }];
for (const data of steps) {
  script.push({ data: { publicData: stringify(data) } });
  script.push({ probe });
}

const result = await runWidget(html, {
  data: { publicData: stringify(initial) },
  script,
  timerRounds: 0,
});
assert.equal(result.stopped, undefined, 'Widget runtime stopped');
assert.ok(result.watchesPublicData, 'Widget did not subscribe to data');
assert.equal(result.requests.length, 0, 'Unexpected network request');
assert.equal(result.probes.length, 5, 'State-change probes were missing');

const stages: [string, Partial<Visible>][] = [
  ['initial', {
    voltage: '223.5', phaseL2: '224.1 V', phaseL3: '222.2 V',
    multiHidden: false, axisHidden: false, groupHidden: false,
    cells: 48, state: 'CONNECTED', label: 'Voltage',
  }],
  ['offline Russian', {
    voltage: '—', multiHidden: true,
    axisHidden: true, groupHidden: true, cells: 0,
    state: 'НЕТ СВЯЗИ', label: 'Напряжение',
    source: 'График не загружен',
  }],
  ['recovered Ukrainian', {
    voltage: '225.2', phaseL2: '—', phaseL3: '223.9 В',
    multiHidden: false, axisHidden: false, groupHidden: false,
    cells: 48, state: "НА ЗВ'ЯЗКУ", label: 'Напруга',
  }],
  ['expired schedule', {
    voltage: '225.2', axisHidden: true,
    groupHidden: true, cells: 0, source: 'Графік застарів',
  }],
  ['one phase again', {
    voltage: '221.8', multiHidden: true,
    axisHidden: true, groupHidden: true, cells: 0,
    state: 'CONNECTED', label: 'Voltage',
  }],
];
for (let index = 0; index < stages.length; index++) {
  const [label, expected] = stages[index];
  const actual = parse(result.probes[index]);
  for (const [key, value] of Object.entries(expected)) {
    assert.deepEqual(
      actual[key as keyof Visible], value,
      label + ' / ' + key + ': actual=' + JSON.stringify(actual),
    );
  }
  console.log('PASS widget lifecycle: ' + label);
}
console.log('PASS Telegram QuickJS live public_data update lifecycle');
