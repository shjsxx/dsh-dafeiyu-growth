'use strict';
import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import os from 'node:os';
import fs from 'node:fs';
import { safeFile, within, PresentationHost, COMMANDS, STATES } from '../desktop/presentation-host.cjs';

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'yunlan-host-'));
fs.mkdirSync(path.join(root, 'assets', 'webm'), { recursive: true });
fs.writeFileSync(path.join(root, 'assets', 'webm', 'a.webm'), 'video');
fs.copyFileSync(path.join(process.cwd(), 'desktop', 'config.cjs'), path.join(root, 'config.cjs'));
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { buildConfig } = require(path.join(root, 'config.cjs'));

test('asset resolution stays inside the pinned runtime', () => {
  assert.equal(safeFile(path.join(root, 'assets'), 'webm/a.webm'), path.join(root, 'assets', 'webm', 'a.webm'));
  for (const name of ['../config.jsonc', '..\\config.jsonc', 'C:\\Windows\\win.ini', '/etc/passwd', 'webm/../../x', '']) {
    assert.equal(safeFile(path.join(root, 'assets'), name), null);
  }
  assert.equal(within(root, path.join(root, 'a')), true);
  assert.equal(within(root, path.dirname(root)), false);
});

test('presentation payloads are bounded and typed', () => {
  const cfg = { renderConfig: { main: { pets: [{ id: 'yunlan' }] } }, stateMap: { SUCCESS: 'celebrate' }, intentMap: { run: 'move' } };
  const host = new PresentationHost(cfg);
  assert.equal(host.apply({ kind: 'presentation', seq: 1, text: '完成啦', state: 'SUCCESS', ttlMs: 4000 }), true);
  assert.equal(host.apply({ kind: 'presentation', seq: 1, text: '重复序号', state: 'SUCCESS', ttlMs: 4000 }), false);
  assert.equal(host.apply({ kind: 'presentation', seq: 2, text: 'x', state: 'EVIL', ttlMs: 4000 }), false);
  assert.equal(host.apply({ kind: 'presentation', seq: 2, text: 'x', state: 'IDLE', ttlMs: 1e9 }), false);
  assert.equal(host.apply({ kind: 'presentation', seq: 2, text: 'x'.repeat(2001), state: 'IDLE', ttlMs: 4000 }), false);
  assert.equal(host.apply({ kind: 'clear' }), true);
  assert.deepEqual(host.state.pets.yunlan.say.data, { kind: 'clear' });
  assert.equal(host.apply({ kind: 'presentation', seq: 3, text: '', state: 'IDLE', ttlMs: 100, intent: 'run' }), true);
  assert.equal(host.state.pets.yunlan.anim.data.name, 'move');
});

test('sleep state routes persist and pause presentations', () => {
  const sleepFile = path.join(root, 'sleep.json');
  const cfg = {
    renderConfig: { main: { animations: { sleep: [{ start: ['a'], loop: ['b'], end: ['c'] }] } } },
    stateMap: {}, intentMap: {}, sleepFile,
  };
  const base = 'dsh-pet-bridge://dsh-pet/dsh-pet-7340';
  const host = new PresentationHost(cfg);
  assert.equal(host.route(new URL(base + '/sleep?pet=yunlan'), 'POST').status, 200);
  assert.deepEqual(host.state.pets.yunlan.sleep.data, { sleeping: true, start: 'a', loop: 'b', end: 'c' });
  assert.equal(host.apply({ kind: 'presentation', seq: 1, text: '任务完成', state: 'SUCCESS', ttlMs: 4000 }), false);
  const restored = new PresentationHost(cfg);
  assert.deepEqual(restored.state.pets.yunlan.sleep.data, { sleeping: true, start: 'a', loop: 'b', end: 'c' });
  assert.equal(restored.route(new URL(base + '/wake?pet=yunlan'), 'POST').status, 200);
  assert.deepEqual(restored.state.pets.yunlan.sleep.data, { sleeping: false });
  assert.equal(restored.apply({ kind: 'presentation', seq: 1, text: '任务完成', state: 'SUCCESS', ttlMs: 4000 }), true);
  assert.equal(restored.route(new URL(base + '/sleep?pet=other'), 'POST').status, 400);
});

test('routes expose only the renderer essentials', () => {
  const cfg = { renderConfig: { main: {} }, stateMap: {}, intentMap: {} };
  const host = new PresentationHost(cfg);
  const base = 'dsh-pet-bridge://dsh-pet/dsh-pet-7340';
  assert.equal(host.route(new URL(base + '/config'), 'GET').status, 200);
  assert.equal(host.route(new URL(base + '/state'), 'GET').status, 200);
  assert.equal(host.route(new URL(base + '/bubble/clear'), 'POST').status, 403);
  assert.equal(host.route(new URL(base + '/whisper'), 'POST').status, 403);
  assert.equal(host.route(new URL(base + '/messages'), 'GET').status, 403);
  assert.equal(host.route(new URL(base + '/playback/yunlan-1/report'), 'POST', { status: 'ended' }).status, 400);
  assert.equal(COMMANDS.has('open-menu'), true);
  assert.equal(COMMANDS.has('eval'), false);
  assert.equal(STATES.has('SUCCESS'), true);
});
