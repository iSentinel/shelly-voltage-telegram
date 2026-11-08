/* Acceptance of Telegram bubble layout using the upstream native renderer. */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runWidget } from './sandbox-node.ts';

const file = process.env.SHELLY_WIDGET_HTML;
assert.ok(file, 'SHELLY_WIDGET_HTML is required');
const html = readFileSync(file, 'utf8');
const stamp = Math.floor(Date.now() / 1000) - 3;

function publicState(multiphase: boolean): string {
  const state: Record<string, unknown> = {
    schema: 1, l: 'uk', s: 'online', v: 223.8, f: 50, t: stamp,
    g: '10.1',
  };
  if (multiphase) {
    state.p = {
      L2: { s: 'online', t: stamp, v: 222.3, f: 50 },
      L3: { s: 'offline' },
    };
  }
  return JSON.stringify(state);
}

interface LayoutCase {
  width: number;
  font: number;
  theme: 'light' | 'dark';
  phases: boolean;
}

const cases: LayoutCase[] = [
  { width: 200, font: 16, theme: 'light', phases: false },
  { width: 200, font: 22, theme: 'dark', phases: true },
  { width: 320, font: 16, theme: 'light', phases: true },
  { width: 320, font: 24, theme: 'dark', phases: true },
  { width: 430, font: 16, theme: 'dark', phases: false },
  { width: 600, font: 24, theme: 'light', phases: true },
];

for (const scenario of cases) {
  const result = await runWidget(html, {
    width: scenario.width,
    layout: true,
    theme: scenario.theme,
    text: { fontSize: scenario.font },
    data: { publicData: publicState(scenario.phases) },
  });
  const label = JSON.stringify(scenario);
  assert.equal(result.stopped, undefined, label + ': runtime stopped');
  assert.ok(result.watchesPublicData, label + ': no public-data updates');
  assert.ok(
    Number.isFinite(result.height),
    label + ': real renderer did not return a height',
  );
  assert.ok(
    result.height! >= 50 && result.height! <= 600,
    label + ': content height ' + result.height + ' clips in Telegram',
  );
  assert.ok(result.nodes < 2000, label + ': node limit exceeded');
  console.log('PASS layout ' + label + ': ' + result.height + ' px');
}
console.log('PASS layout scenarios (real Telegram renderer core)');
