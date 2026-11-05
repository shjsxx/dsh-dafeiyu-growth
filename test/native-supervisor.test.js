import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import {
  NativeSupervisor,
  acquireLock,
  bundleStatus,
  releaseLock,
  rendererConfig,
  resolveBundle,
} from '../src/native-supervisor.js'

function tempRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'yunlan-supervisor-'))
}

function completeBundle(root) {
  const files = {
    'desktop/main.cjs': '// host',
    'runtime/electron/DeepSeek Harness.exe': 'electron',
    'runtime/helper/dsh-dafeiyu-helper.exe': 'helper',
    'runtime/prepared-3c99b1b/runtime/electron-helper/index.html': 'html',
    'runtime/models/bge-small-zh-v1.5/model.onnx': 'model',
  }
  for (const [name, value] of Object.entries(files)) {
    const file = path.join(root, name)
    fs.mkdirSync(path.dirname(file), { recursive: true })
    fs.writeFileSync(file, value)
  }
}

function fakeChild() {
  const child = new EventEmitter()
  child.pid = 4321
  child.stderr = new EventEmitter()
  child.kill = () => child.emit('exit', 0, null)
  return child
}

test('missing runtime is inert unless explicitly required', () => {
  const root = tempRoot()
  const env = { ...process.env, DSH_YUNLAN_RENDERER_DIR: path.join(root, 'renderer') }
  const calls = []
  const supervisor = new NativeSupervisor({
    root, env, config: {}, spawnImpl: (...args) => { calls.push(args); return fakeChild() },
  })
  assert.equal(supervisor.start(), false)
  assert.equal(calls.length, 0)
  assert.deepEqual(bundleStatus(supervisor.paths), {
    electron: false, helper: false, prepared: false, host: false, complete: false,
  })
  assert.equal(supervisor.start(), false)
})

test('complete bundle starts helper with the external renderer marker', () => {
  const root = tempRoot()
  completeBundle(root)
  const env = { ...process.env, DSH_YUNLAN_RENDERER_DIR: path.join(root, 'renderer') }
  let spawned = null
  const child = fakeChild()
  const supervisor = new NativeSupervisor({
    root, env, config: {}, spawnImpl: (command, args, options) => {
      spawned = { command, args, options }
      return child
    },
  })
  assert.equal(supervisor.start(), true)
  assert.match(spawned.command, /dsh-dafeiyu-helper\.exe$/)
  assert.deepEqual(spawned.args, ['--external-renderer', '--renderer-config', supervisor.paths.marker])
  assert.equal(spawned.options.windowsHide, true)
  assert.equal(spawned.options.stdio[0], 'pipe')
  assert.equal(spawned.options.env.DSH_DAFEIYU_EMBEDDING_DIR, supervisor.paths.models)
  const marker = JSON.parse(fs.readFileSync(supervisor.paths.marker, 'utf8'))
  assert.deepEqual(marker, rendererConfig(supervisor.paths))
  assert.equal(supervisor.status().running, true)
  supervisor.stop()
  assert.equal(fs.existsSync(supervisor.paths.marker), false)
  assert.equal(fs.existsSync(supervisor.paths.lock), false)
})

test('a live supervisor lock prevents a duplicate pet', () => {
  const root = tempRoot()
  completeBundle(root)
  const env = { ...process.env, DSH_YUNLAN_RENDERER_DIR: path.join(root, 'renderer') }
  let spawns = 0
  const first = new NativeSupervisor({
    root, env, spawnImpl: () => { spawns += 1; return fakeChild() },
  })
  const second = new NativeSupervisor({
    root, env, spawnImpl: () => { spawns += 1; return fakeChild() },
  })
  assert.equal(first.start(), true)
  assert.equal(second.start(), false)
  assert.equal(spawns, 1)
  first.stop()
})

test('a normal helper exit is not treated as a crash loop', () => {
  const root = tempRoot()
  completeBundle(root)
  const env = { ...process.env, DSH_YUNLAN_RENDERER_DIR: path.join(root, 'renderer') }
  let spawns = 0
  const child = fakeChild()
  const supervisor = new NativeSupervisor({
    root, env, spawnImpl: () => { spawns += 1; return child },
  })
  assert.equal(supervisor.start(), true)
  child.emit('exit', 0, null)
  assert.equal(spawns, 1)
  supervisor.stop()
})

test('disabled config never touches the marker', () => {
  const root = tempRoot()
  completeBundle(root)
  const env = { ...process.env, DSH_YUNLAN_RENDERER_DIR: path.join(root, 'renderer') }
  let spawns = 0
  const supervisor = new NativeSupervisor({
    root, env, config: { nativeBundle: false }, spawnImpl: () => { spawns += 1; return fakeChild() },
  })
  assert.equal(supervisor.start(), false)
  assert.equal(spawns, 0)
  assert.equal(fs.existsSync(supervisor.paths.marker), false)
})

test('stale locks can be recovered and release is owner-scoped', () => {
  const dir = tempRoot()
  const lock = path.join(dir, 'supervisor.lock')
  fs.writeFileSync(lock, JSON.stringify({ pid: 99999999, token: 'old', started: 1 }))
  assert.equal(acquireLock(lock, 1234, 'a'), true)
  releaseLock(lock, 'b')
  assert.equal(fs.existsSync(lock), true)
  releaseLock(lock, 'a')
  assert.equal(fs.existsSync(lock), false)
})

test('resolveBundle honors explicit overrides', () => {
  const root = tempRoot()
  const paths = resolveBundle(root, {
    DSH_YUNLAN_ELECTRON: 'C:/e/electron.exe',
    DSH_YUNLAN_HELPER: 'C:/h/helper.exe',
    DSH_YUNLAN_PREPARED: 'C:/r/prepared',
    DSH_YUNLAN_RENDERER_DIR: 'C:/u/renderer',
  })
  assert.equal(paths.electron, 'C:/e/electron.exe')
  assert.equal(paths.helper, 'C:/h/helper.exe')
  assert.equal(paths.prepared, 'C:/r/prepared')
  assert.equal(paths.marker, path.join('C:/u/renderer', 'active.json'))
})
