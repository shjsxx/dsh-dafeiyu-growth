import test from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { mkdtempSync, writeFileSync, readFileSync, existsSync, readdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { apply, STATE_ENDPOINT, ACTION_ENDPOINT } from '../src/index.js'
import { emptyProfile, loadProfile, observe, derive, recomputeLearned } from '../src/growth.js'
import { queuePathForProfile } from '../src/store.js'

async function host(t, { config, seed, queue, corrupt = false, folder: fixtureFolder, filename = 'profile.json' } = {}) {
  const folder = fixtureFolder ?? mkdtempSync(join(tmpdir(), 'growth-server-test-'))
  const file = join(folder, filename)
  const queueFile = queuePathForProfile(file)
  if (seed) writeFileSync(file, JSON.stringify(seed))
  if (corrupt) writeFileSync(file, '{broken')
  if (queue) writeFileSync(queueFile, queue)
  const previous = process.env.DSH_DAFEIYU_GROWTH_FILE
  process.env.DSH_DAFEIYU_GROWTH_FILE = file
  const routes = new Map()
  const events = new Map()
  const cleanup = []
  const warnings = []
  try { await apply({
    logger: { warn: (message) => warnings.push(message) },
    on(name, handler) { events.set(name, handler); return () => events.delete(name) },
    webServer: { register({ path, handler }) { routes.set(path, handler); return () => routes.delete(path) } },
    effect(factory) { cleanup.push(factory()) },
  }, config) } finally {
    if (previous === undefined) delete process.env.DSH_DAFEIYU_GROWTH_FILE
    else process.env.DSH_DAFEIYU_GROWTH_FILE = previous
  }
  t.after(() => cleanup.forEach((fn) => fn()))
  const request = async (path, { method = path === STATE_ENDPOINT ? 'GET' : 'POST', body, headers = {}, address = '127.0.0.1' } = {}) => {
    const req = new EventEmitter()
    req.method = method
    req.socket = { remoteAddress: address }
    req.headers = { host: '127.0.0.1:60630', 'content-type': 'application/json', ...headers }
    req.destroy = () => {}
    const res = { setHeader() {}, end(text) { this.body = JSON.parse(text) } }
    const result = routes.get(path)(req, res)
    if (body !== undefined) req.emit('data', Buffer.from(JSON.stringify(body)))
    req.emit('end')
    await result
    return { status: res.statusCode, body: res.body }
  }
  return { file, folder, queueFile, routes, events, warnings, request }
}

test('disabled config installs no event listeners or routes and does not touch profile', async (t) => {
  const h = await host(t, { config: { enabled: false } })
  assert.equal(h.events.size, 0)
  assert.equal(h.routes.size, 0)
  assert.equal(existsSync(h.file), false)
})

test('routes reject cross-origin, cross-site, non-local, non-JSON and wrong methods', async (t) => {
  const h = await host(t)
  for (const options of [
    { headers: { origin: 'https://evil.test' } },
    { headers: { origin: 'null' } },
    { headers: { 'sec-fetch-site': 'cross-site' } },
    { headers: { host: 'evil.test' } },
    { address: '192.168.1.2' },
  ]) {
    assert.equal((await h.request(ACTION_ENDPOINT, { ...options, body: { type: 'reset' } })).status, 403)
  }
  assert.equal((await h.request(ACTION_ENDPOINT, { headers: { 'content-type': 'text/plain' }, body: { type: 'reset' } })).status, 415)
  assert.equal((await h.request(STATE_ENDPOINT, { method: 'POST' })).status, 405)
  assert.equal((await h.request(ACTION_ENDPOINT, { method: 'GET' })).status, 405)
  assert.equal((await h.request(ACTION_ENDPOINT, { headers: { origin: 'http://127.0.0.1:60630' }, body: { type: 'diary', text: 'safe' } })).status, 200)
})

test('reset backs up before clearing; corrupt profile fails closed without data loss', async (t) => {
  const seed = emptyProfile()
  seed.xp = 500
  const h = await host(t, { seed })
  const response = await h.request(ACTION_ENDPOINT, { body: { type: 'reset' } })
  assert.equal(response.status, 200)
  assert.equal(response.body.state.xp, 0)
  const backup = readdirSync(h.folder).find((file) => file.endsWith('.bak'))
  assert.equal(JSON.parse(readFileSync(join(h.folder, backup), 'utf8')).xp, 500)
  const broken = await host(t, { corrupt: true })
  assert.equal((await broken.request(ACTION_ENDPOINT, { body: { type: 'reset' } })).status, 503)
  assert.equal((await broken.request(ACTION_ENDPOINT, { body: { type: 'diary', text: 'x' } })).status, 503)
  assert.equal(readFileSync(broken.file, 'utf8'), '{broken')
})

test('queue drains on startup with original diary timestamp, and commits before acknowledgement', async (t) => {
  const at = '2026-10-30T01:00:00.000Z'
  const text = `${JSON.stringify({ type: 'diary', text: 'queued', at })}\n`
  const h = await host(t, { queue: text })
  const saved = JSON.parse(readFileSync(h.file, 'utf8'))
  assert.equal(saved.diary[0].at, at)
  assert.equal(existsSync(`${h.queueFile}.processing`), false)
  // Simulate crash after commit but before removal of the claimed batch.
  writeFileSync(`${h.queueFile}.processing`, text)
  assert.equal((await h.request(STATE_ENDPOINT)).body.diary.length, 1)
  assert.equal(existsSync(`${h.queueFile}.processing`), false)
  writeFileSync(h.queueFile, `${JSON.stringify({ type: 'diary', text: 'next' })}\n`)
  assert.equal((await h.request(STATE_ENDPOINT)).body.diary[0].text, 'next')
})

test('queue record ids remain idempotent when retried inside a different batch', async (t) => {
  const first = { id: 'stable-record', type: 'diary', text: 'once' }
  const h = await host(t, { queue: `${JSON.stringify(first)}\n` })
  writeFileSync(h.queueFile, `${JSON.stringify(first)}\n${JSON.stringify({ id: 'new-record', type: 'diary', text: 'new' })}\n`)
  const state = (await h.request(STATE_ENDPOINT)).body
  assert.equal(state.diary.length, 2)
  assert.equal(state.diary.filter((row) => row.text === 'once').length, 1)
})

test('failed queue commit retains batch and original corrupted profile', async (t) => {
  const h = await host(t, { corrupt: true, queue: '{"type":"diary","text":"preserve"}\n' })
  assert.equal(existsSync(`${h.queueFile}.processing`), true)
  assert.equal(readFileSync(h.file, 'utf8'), '{broken')
  assert.equal((await h.request(STATE_ENDPOINT)).body.diary.length, 0)
})

test('legacy unconfirmed queued resets are ignored; partial final records are retained', async (t) => {
  const seed = emptyProfile()
  seed.xp = 200
  const h = await host(t, { seed, queue: '{"type":"reset"}\n' })
  assert.equal((await h.request(STATE_ENDPOINT)).body.xp, 200)
  writeFileSync(h.queueFile, '{"type":"diary","text":"unfinished"}')
  await h.request(STATE_ENDPOINT)
  assert.equal(existsSync(`${h.queueFile}.processing`), true)
  assert.equal((await h.request(STATE_ENDPOINT)).body.diary.length, 0)
})

test('stable event identities are idempotent across reload and scoped to session/type', () => {
  let profile = emptyProfile()
  const call = { type: 'tool/call', seq: 7, data: { name: 'read' } }
  observe(profile, { event: call, session: { id: 'a' } })
  profile = loadProfile(JSON.parse(JSON.stringify(profile)))
  observe(profile, { event: call, session: { id: 'a' } })
  assert.equal(profile.counters.toolCalls, 1)
  observe(profile, { event: call, session: { id: 'b' } })
  assert.equal(profile.counters.toolCalls, 2)
  observe(profile, { event: { type: 'tool/result', seq: 7, data: { error: true } }, session: { id: 'a' } })
  assert.equal(profile.counters.toolErrors, 1)
})

test('stream deltas are not activity, legitimate identical calls without ids still count', () => {
  const profile = emptyProfile()
  observe(profile, { event: { type: 'text/delta', data: { text: 'hello' } } })
  assert.equal(profile.xp, 0)
  assert.equal(profile.counters.events, 0)
  for (let i = 0; i < 2; i++) observe(profile, { event: { type: 'tool/call', data: { name: '__proto__' } } })
  assert.equal(profile.tools.__proto__, 2)
  assert.equal(profile.counters.toolCalls, 2)
})

test('malformed numeric profile fields do not poison counters or derived traits', () => {
  const profile = loadProfile({ xp: -20, activeMs: -1, counters: { turns: '10', okTurns: null }, hours: Array(24).fill(-1), tools: { read: '10' } })
  observe(profile, { event: { type: 'turn/end', data: { reason: { kind: 'completed' } } } })
  assert.equal(profile.counters.turns, 1)
  for (const value of Object.values(derive(profile).traits)) assert.ok(Number.isFinite(value) && value >= 0 && value <= 100)
})

test('reset retains delivery watermarks so old events cannot resurrect cleared progress', async (t) => {
  const h = await host(t)
  const event = { type: 'tool/call', seq: 42, data: { name: 'read' } }
  h.events.get('session/event')({ id: 's' }, event)
  assert.equal((await h.request(STATE_ENDPOINT)).body.counters.toolCalls, 1)
  assert.equal((await h.request(ACTION_ENDPOINT, { body: { type: 'reset' } })).status, 200)
  h.events.get('session/event')({ id: 's' }, event)
  assert.equal((await h.request(STATE_ENDPOINT)).body.counters.toolCalls, 0)
})

test('distinct approvals within one turn are not collapsed', () => {
  const profile = emptyProfile()
  for (const approvalId of ['a', 'b']) observe(profile, {
    event: { type: 'approval/decided', data: { approvalId, turnId: 'same-turn' } }, session: { id: 's' },
  })
  assert.equal(profile.counters.approvals, 2)
})

test('a producer appending to the live queue is not erased when an older batch commits', async (t) => {
  const h = await host(t)
  writeFileSync(`${h.queueFile}.processing`, '{"type":"diary","text":"older"}\n')
  writeFileSync(h.queueFile, '{"type":"diary","text":"new append"}\n')
  assert.equal((await h.request(STATE_ENDPOINT)).body.diary[0].text, 'older')
  assert.equal(existsSync(h.queueFile), true)
  assert.equal((await h.request(STATE_ENDPOINT)).body.diary[0].text, 'new append')
  assert.equal(JSON.parse(readFileSync(h.file, 'utf8')).diary.length, 2)
})

test('daily streak uses calendar days across spring DST', () => {
  const before = process.env.TZ
  try {
    process.env.TZ = 'America/New_York'
    const now = new Date(2026, 2, 9, 0, 30).getTime()
    const profile = emptyProfile(now)
    profile.days = { '2026-03-09': 1, '2026-03-08': 1, '2026-03-07': 1 }
    assert.equal(derive(profile, now).streak, 3)
  } finally {
    if (before === undefined) delete process.env.TZ
    else process.env.TZ = before
  }
})

test('recovery requires a preceding failure in the same session, including after reload', () => {
  let profile = emptyProfile()
  observe(profile, { session: { id: 'A' }, event: { type: 'tool/result', data: { error: true } } })
  const before = profile.xp
  observe(profile, { session: { id: 'B' }, event: { type: 'tool/result', data: {} } })
  assert.equal(profile.counters.recoveries, 0)
  assert.equal(profile.errorStreak, 1)
  assert.equal(profile.xp, before)
  profile = loadProfile(JSON.parse(JSON.stringify(profile)))
  observe(profile, { session: { id: 'A' }, event: { type: 'tool/result', data: {} } })
  assert.equal(profile.counters.recoveries, 1)
  assert.equal(profile.errorStreak, 0)
  assert.equal(profile.xp, before + 8)
})

test('legacy unattributed error streak is retained without rewarding arbitrary sessions', () => {
  const profile = loadProfile({ errorStreak: 3, bestErrorStreak: 5 })
  observe(profile, { session: { id: 'B' }, event: { type: 'tool/result', data: {} } })
  assert.equal(profile.counters.recoveries, 0)
  assert.equal(profile.errorStreak, 3)
  assert.equal(profile.bestErrorStreak, 5)
  observe(profile, { session: { id: 'A' }, event: { type: 'tool/result', data: { error: true } } })
  assert.equal(profile.errorStreak, 4)
  observe(profile, { session: { id: 'A' }, event: { type: 'tool/result', data: {} } })
  assert.equal(profile.counters.recoveries, 1)
  assert.equal(profile.errorStreak, 3)
})

test('learned predicates are current, read-time refresh is pure, and peak hour describes activity', () => {
  const now = new Date(2026, 9, 30, 12).getTime()
  const profile = emptyProfile(now)
  profile.days = { '2026-10-30': 1, '2026-10-29': 1 }
  profile.hours[23] = 40
  recomputeLearned(profile, now)
  assert.ok(profile.learned.some((row) => row.id === 'streak'))
  assert.ok(profile.learned.some((row) => row.id === 'night'))
  assert.equal(profile.learned.find((row) => row.id === 'hour').text, '你最常在 23 点活跃')
  const unchangedAt = profile.learned.find((row) => row.id === 'hour').at
  recomputeLearned(profile, now + 1000)
  assert.equal(profile.learned.find((row) => row.id === 'hour').at, unchangedAt)
  profile.hours = Array(24).fill(0)
  profile.hours[12] = 100
  const persisted = JSON.stringify(profile)
  const later = new Date(2026, 10, 3, 12).getTime()
  const current = derive(profile, later)
  assert.equal(current.learned.some((row) => row.id === 'streak' || row.id === 'night'), false)
  assert.equal(current.learned.find((row) => row.id === 'hour').text, '你最常在 12 点活跃')
  assert.equal(JSON.stringify(profile), persisted)
  recomputeLearned(profile, later)
  assert.equal(profile.learned.some((row) => row.id === 'streak' || row.id === 'night'), false)
})

test('custom profiles in the same directory receive only their own CLI queue', async (t) => {
  const folder = mkdtempSync(join(tmpdir(), 'growth-custom-queue-test-'))
  const a = await host(t, { folder, filename: 'alice.json' })
  const b = await host(t, { folder, filename: 'bob.json' })
  const cli = fileURLToPath(new URL('../tools/growth.mjs', import.meta.url))
  const env = { ...process.env, DSH_DAFEIYU_GROWTH_FILE: a.file }
  assert.equal(spawnSync(process.execPath, [cli, 'review', 'only alice'], { env, stdio: 'ignore' }).status, 0)
  assert.equal(existsSync(join(folder, 'alice.json.pending-diary.jsonl')), true)
  assert.equal(existsSync(join(folder, 'pending-diary.jsonl')), false)
  assert.equal((await b.request(STATE_ENDPOINT)).body.diary.length, 0)
  assert.equal((await a.request(STATE_ENDPOINT)).body.diary[0].text, 'only alice')
  assert.equal((await b.request(STATE_ENDPOINT)).body.diary.length, 0)
})

test('state reports storage health instead of presenting corrupt storage as healthy empty state', async (t) => {
  const healthy = await host(t)
  assert.deepEqual((await healthy.request(STATE_ENDPOINT)).body.storageHealth, { ok: true, readable: true, writable: true, error: null })
  const broken = await host(t, { corrupt: true })
  const state = (await broken.request(STATE_ENDPOINT)).body
  assert.equal(state.storageHealth.ok, false)
  assert.equal(state.storageHealth.readable, false)
  assert.equal(state.storageHealth.writable, false)
  assert.equal(typeof state.storageHealth.error, 'string')
})

test('CLI reset requires explicit confirmation and writes only isolated queue', () => {
  const folder = mkdtempSync(join(tmpdir(), 'growth-cli-test-'))
  const file = join(folder, 'profile.json')
  const cli = fileURLToPath(new URL('../tools/growth.mjs', import.meta.url))
  const env = { ...process.env, DSH_DAFEIYU_GROWTH_FILE: file }
  assert.equal(spawnSync(process.execPath, [cli, 'reset'], { env, stdio: 'ignore' }).status, 2)
  assert.equal(existsSync(join(folder, 'pending-diary.jsonl')), false)
  assert.equal(spawnSync(process.execPath, [cli, 'reset', '--confirm'], { env, stdio: 'ignore' }).status, 0)
  assert.equal(JSON.parse(readFileSync(join(folder, 'pending-diary.jsonl'), 'utf8')).confirm, true)
  assert.equal(existsSync(file), false)
})
