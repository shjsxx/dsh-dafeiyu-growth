import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, writeFileSync, readdirSync, renameSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createStore, queuePathForProfile } from '../src/store.js'

function fixture() { return join(mkdtempSync(join(tmpdir(), 'growth-store-test-')), 'profile.json') }

test('corrupt and unreadable profiles are preserved even on flush', () => {
  const file = fixture()
  writeFileSync(file, '{broken')
  const warnings = []
  const store = createStore(file, { warn: (message) => warnings.push(message) })
  store.set({ xp: 0 })
  assert.equal(store.save({ immediate: true }), false)
  assert.equal(store.flush(), false)
  assert.equal(readFileSync(file, 'utf8'), '{broken')
  assert.ok(warnings.length)
  store.dispose()
  const denied = createStore(file, { readFile() { throw Object.assign(new Error('denied'), { code: 'EACCES' }) } })
  denied.set({ xp: 0 })
  assert.equal(denied.flush(), false)
})

test('failed atomic write returns false and preserves last committed data', () => {
  const file = fixture()
  writeFileSync(file, '{"xp":12}')
  const store = createStore(file, { moveFile() { throw new Error('locked') } })
  store.set({ xp: 20 })
  assert.equal(store.save({ immediate: true }), false)
  assert.equal(JSON.parse(readFileSync(file, 'utf8')).xp, 12)
  assert.equal(readdirSync(join(file, '..')).filter((name) => name.endsWith('.tmp')).length, 0)
  store.dispose()
})

test('backup captures in-memory state and disposed store cannot write', () => {
  const file = fixture()
  const store = createStore(file)
  store.set({ xp: 22 })
  const backup = store.backup()
  assert.equal(JSON.parse(readFileSync(backup, 'utf8')).xp, 22)
  assert.equal(store.save({ immediate: true }), true)
  store.dispose()
  store.set({ xp: 999 })
  assert.equal(store.flush(), false)
  assert.equal(JSON.parse(readFileSync(file, 'utf8')).xp, 22)
})

test('queue names preserve the default legacy path and namespace custom profile names', () => {
  const folder = join(tmpdir(), 'queue-names')
  assert.equal(queuePathForProfile(join(folder, 'profile.json')), join(folder, 'pending-diary.jsonl'))
  assert.equal(queuePathForProfile(join(folder, 'alice.json')), join(folder, 'alice.json.pending-diary.jsonl'))
  assert.notEqual(queuePathForProfile(join(folder, 'alice.json')), queuePathForProfile(join(folder, 'bob.json')))
})

test('storage health exposes and clears transient commit failures', () => {
  const file = fixture()
  let fail = true
  const store = createStore(file, { moveFile(from, to) { if (fail) throw new Error('locked'); renameSync(from, to) } })
  assert.equal(store.health().ok, true)
  store.set({ xp: 1 })
  assert.equal(store.flush(), false)
  assert.deepEqual(store.health(), { ok: false, readable: true, writable: false, error: 'locked' })
  fail = false
  assert.equal(store.flush(), true)
  assert.deepEqual(store.health(), { ok: true, readable: true, writable: true, error: null })
  store.dispose()
})

test('flush without modifications does not rewrite an existing profile', () => {
  const file = fixture()
  writeFileSync(file, '{"xp":7}')
  let writes = 0
  const store = createStore(file, { writeFile() { writes += 1 } })
  assert.equal(store.flush(), true)
  assert.equal(writes, 0)
  store.dispose()
})
