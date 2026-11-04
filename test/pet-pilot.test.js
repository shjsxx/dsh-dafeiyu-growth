import test from 'node:test'
import assert from 'node:assert/strict'
import { createDshPetPilot, DSH_PET_CONTRACT, PET_DEMO_SCENES } from '../src/pet/dsh-pet-pilot.js'
import { createPetPilotServer } from './helpers/pet-pilot-server.js'
import { parseArgs, HELP, main } from '../tools/pet-pilot.mjs'

const config = baseUrl => ({ enabled: true, acknowledgeUnauthenticatedLocalApi: true,
  contractCommit: DSH_PET_CONTRACT.commit, baseUrl, petId: 'yunlan-pilot',
  animations: { greet: '演示点头', celebrate: '演示庆祝' }, requestTimeoutMs: 100, observeMs: 30, leaseMs: 5000 })
async function fixture(t, options = {}, overrides = {}) {
  const server = await createPetPilotServer(options)
  const pilot = createDshPetPilot({ ...config(server.baseUrl), ...overrides })
  t.after(async () => { await pilot.close(); await server.close() })
  return { server, pilot }
}
const route = entry => entry.path.replace(`${DSH_PET_CONTRACT.prefix}/`, '')
function deferred() { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }

test('disabled by default: all entry points produce zero traffic', async t => {
  const { server } = await fixture(t)
  const pilot = createDshPetPilot({ ...config(server.baseUrl), enabled: undefined })
  assert.equal(pilot.status().enabled, false)
  assert.equal((await pilot.present()).code, 'disabled')
  await pilot.cancel(); await pilot.close()
  assert.equal(server.requests.length, 0)
})

test('strict endpoint, contract, dedicated pet, acknowledgement and budgets reject before IO', async t => {
  const { server } = await fixture(t)
  const invalid = [
    ...['http://localhost:1234', 'https://127.0.0.1:1234', 'http://127.0.0.1:1234/', 'http://127.0.0.1:1234?x=1', 'http://127.0.0.1:1234#x', 'http://127.0.0.1:0', 'http://127.0.0.1:65536', 'http://127.0.0.1:0123', 'http://user@127.0.0.1:1234', 'http://[::1]:1234'].map(baseUrl => ({ baseUrl })),
    { contractCommit: 'other' }, { acknowledgeUnauthenticatedLocalApi: false },
    ...['main', '', '../other', 'x&pet=main'].map(petId => ({ petId })),
    { requestTimeoutMs: 49 }, { requestTimeoutMs: 3001 }, { observeMs: -1 }, { observeMs: 5000, leaseMs: 5000 },
    { animations: { greet: '../raw' } }, { animations: { other: 'anything' } },
  ]
  for (const patch of invalid) {
    const pilot = createDshPetPilot({ ...config(server.baseUrl), ...patch })
    assert.equal(pilot.status().enabled, false, JSON.stringify(patch))
    assert.equal((await pilot.present()).ok, false)
    await pilot.close()
  }
  assert.equal(server.requests.length, 0)
})

test('only fixed synthetic scene identifiers are accepted', async t => {
  const { pilot, server } = await fixture(t)
  for (const input of ['real private text', '../greeting', 'toString', '', { text: 'private' }, { toString: () => 'greeting' }]) assert.equal((await pilot.present(input)).code, 'synthetic-scene-required')
  assert.equal(server.requests.length, 0)
})

test('successful bare pinned playback receipts, route whitelist and sanitized reports', async t => {
  const { pilot, server } = await fixture(t)
  const report = await pilot.present('greeting')
  assert.equal(report.ok, true)
  assert.equal(report.code, 'renderer-reported-ended')
  assert.equal(report.playback, 'ended')
  assert.equal(report.animationStopConfirmed, false)
  assert.equal(report.cleanup.bubble, 'clear-host-accepted')
  assert.equal(report.cleanup.lock, 'release-host-accepted')
  const allowed = /^(lock|broadcast|anim|playback\/fixture-playback-private|bubble\/clear|lock\/release)$/
  for (const req of server.requests) {
    assert.match(route(req), allowed)
    assert.equal(req.query.pet, 'yunlan-pilot'); assert.equal(req.query.surface, 'both')
    assert.equal(Object.hasOwn(req.query, 'voiceTo'), false)
    assert.equal(Object.hasOwn(req.body ?? {}, 'voiceTo'), false)
    assert.equal(Object.hasOwn(req.body ?? {}, 'force'), false)
  }
  const broadcast = server.requests.find(r => route(r) === 'broadcast')
  assert.equal(broadcast.body.text, PET_DEMO_SCENES.greeting.text)
  assert.equal(broadcast.body.anim, false)
  assert.equal(server.requests.find(r => route(r) === 'anim').body.name, '演示点头')
  for (const secret of [server.baseUrl, 'fixture-lock-private', 'fixture-playback-private', PET_DEMO_SCENES.greeting.text, 'yunlan-pilot']) assert.equal(JSON.stringify(report).includes(secret), false)
})

test('accepted does not mean confirmed; playing and failed are pinned statuses', async t => {
  for (const status of ['accepted', 'playing', 'failed', 'cancelled']) {
    const { pilot } = await fixture(t, { statuses: [status] })
    const report = await pilot.present('celebrate')
    assert.equal(report.playback, status)
    assert.equal(report.ok, false)
    assert.equal(report.code, ['accepted', 'playing'].includes(status) ? 'playback-unconfirmed' : `renderer-reported-${status}`)
  }
})

test('quiet observes configured window, renews, and sends no animation', async t => {
  const { pilot, server } = await fixture(t, {}, { observeMs: 80 })
  const started = Date.now()
  const report = await pilot.present('quiet')
  assert.ok(Date.now() - started >= 65)
  assert.equal(report.ok, true)
  assert.equal(server.requests.some(r => route(r) === 'anim'), false)
  assert.ok(server.requests.filter(r => route(r) === 'lock').length >= 2)
})

test('contention does not broadcast, release or retry', async t => {
  const { pilot, server } = await fixture(t, { contention: true })
  assert.equal((await pilot.present()).code, 'locked')
  assert.deepEqual(server.requests.map(route), ['lock'])
  assert.equal(pilot.status().reason, 'ready')
})

test('HTTP 200 business rejection is sanitized, not success', async t => {
  const { pilot } = await fixture(t, { onRequest(entry, { send }) {
    if (route(entry) !== 'broadcast') return false
    send({ ok: false, reason: 'PRIVATE_ERROR', raw: 'secret-value' }); return true
  } })
  const report = await pilot.present()
  assert.equal(report.ok, false); assert.equal(report.code, 'upstream-rejected')
  assert.equal(JSON.stringify(report).includes('secret'), false)
  assert.equal(report.cleanup.lock, 'release-host-accepted')
})

test('preempted renewal never reacquires or force releases', async t => {
  const { pilot, server } = await fixture(t, { preempted: true })
  const report = await pilot.present()
  assert.equal(report.ok, false); assert.equal(report.code, 'lock-lost'); assert.equal(report.cause, 'preempted')
  const locks = server.requests.filter(r => route(r) === 'lock')
  assert.equal(locks.filter(r => !r.body.lockId).length, 1)
  for (const entry of server.requests) assert.equal(Object.hasOwn(entry.body ?? {}, 'force'), false)
})

test('cancel while waiting for playback, busy rejection, close and external abort', async t => {
  const seen = deferred()
  const { pilot, server } = await fixture(t, { onRequest(entry, { send }) {
    if (!route(entry).startsWith('playback/')) return false
    seen.resolve(); send({ ignored: true }, { delayMs: 500 }); return true
  } }, { observeMs: 800, requestTimeoutMs: 1000 })
  const controller = new AbortController()
  const running = pilot.present('greeting', { signal: controller.signal })
  await seen.promise
  assert.equal(pilot.status().busy, true)
  assert.equal((await pilot.present()).code, 'busy')
  controller.abort()
  const cancelled = await pilot.cancel()
  assert.equal(cancelled.code, 'cancelled')
  assert.deepEqual(await running, cancelled)
  assert.equal(cancelled.cleanup.lock, 'release-host-accepted')
  await pilot.close()
  assert.equal(pilot.status().reason, 'closed')
  const count = server.requests.length
  assert.equal((await pilot.present()).code, 'closed')
  assert.equal(server.requests.length, count)
})

test('close during active observation awaits cleanup', async t => {
  const seen = deferred()
  const { pilot } = await fixture(t, { onRequest(entry) { if (route(entry) === 'broadcast') seen.resolve(); return false } }, { observeMs: 500 })
  const running = pilot.present('quiet')
  await seen.promise
  const result = await pilot.close()
  assert.equal((await running).code, 'cancelled')
  assert.equal(result.cleanup.lock, 'release-host-accepted')
  assert.equal(pilot.status().busy, false)
})

test('late acquisition cancellation cools down without force or reacquisition', async t => {
  const seen = deferred()
  const { pilot, server } = await fixture(t, { acquireDelayMs: 300, onRequest(entry) { if (route(entry) === 'lock') seen.resolve(); return false } })
  const running = pilot.present()
  await seen.promise
  await pilot.cancel()
  const report = await running
  assert.equal(report.code, 'cancelled')
  assert.equal(report.cleanup.lock, 'unknown-wait-for-lease')
  assert.equal((await pilot.present()).code, 'lease-cooldown')
  assert.deepEqual(server.requests.map(route), ['lock'])
})

test('timeout acquisition enters conservative lease cooldown', async t => {
  const { pilot, server } = await fixture(t, { acquireDelayMs: 300 }, { requestTimeoutMs: 50 })
  assert.equal((await pilot.present()).code, 'timeout')
  assert.equal(pilot.status().reason, 'lease-cooldown')
  assert.equal((await pilot.present()).code, 'lease-cooldown')
  assert.equal(server.requests.length, 1)
})

test('redirects never followed; oversized malformed wrong-MIME records rejected', async t => {
  const cases = [
    [{ status: 302, headers: { location: '/forbidden' } }, 'http-error'],
    [{ raw: '{bad' }, 'invalid-response'],
    [{ raw: '[]' }, 'invalid-response'],
    [{ headers: { 'content-type': 'text/html' }, raw: '{}' }, 'invalid-response'],
    [{ raw: JSON.stringify({ data: 'x'.repeat(17000) }) }, 'response-too-large'],
  ]
  for (const [response, code] of cases) {
    const { pilot, server } = await fixture(t, { onRequest(entry, { send }) { send({}, response); return true } })
    const report = await pilot.present()
    assert.equal(report.code, code)
    assert.equal(report.ok, false)
    assert.equal(server.requests.length, 1)
  }
})

test('CLI defaults to isolated mock and strictly gates live endpoints', () => {
  assert.equal(parseArgs([]).live, false)
  assert.equal(parseArgs(['--mock']).petId, 'yunlan-pilot')
  assert.equal(parseArgs(['--help']).help, true)
  assert.match(HELP, /display=both/); assert.match(HELP, /NOT automatically verify/)
  const valid = ['--endpoint', 'http://127.0.0.1:23456', '--confirm-isolated', '--pet', 'yunlan-pilot', '--contract', DSH_PET_CONTRACT.commit, '--ack-local-api-risk']
  assert.equal(parseArgs(valid).live, true)
  for (const flag of ['--endpoint', '--confirm-isolated', '--pet', '--contract', '--ack-local-api-risk']) {
    const args = [...valid]; const index = args.indexOf(flag)
    args.splice(index, ['--confirm-isolated', '--ack-local-api-risk'].includes(flag) ? 1 : 2)
    assert.throws(() => parseArgs(args), flag)
  }
  for (const args of [['--text', 'private'], ['--scene', 'private'], ['--endpoint', 'http://localhost:1'], [...valid, '--mock'], [...valid, '--pet', 'main'], ['--mock', '--mock'], ['--greet-animation', '../private']]) assert.throws(() => parseArgs(args))
})

test('default budgets are 1000ms requests, 5000ms observation and sufficient lease', async t => {
  const seen = deferred()
  const { pilot, server } = await fixture(t, { onRequest(entry) { if (route(entry) === 'broadcast') seen.resolve(); return false } }, { observeMs: undefined, requestTimeoutMs: undefined, leaseMs: undefined })
  const running = pilot.present('quiet')
  await seen.promise
  await pilot.cancel(); await running
  assert.equal(server.requests.find(r => route(r) === 'broadcast').body.duration, 5000)
  assert.ok(server.requests[0].body.ttl >= 5000 + 4 * 1000)
  const timed = await fixture(t, { onRequest() { return true } }, { requestTimeoutMs: undefined })
  const started = performance.now()
  assert.equal((await timed.pilot.present()).code, 'timeout')
  assert.ok(performance.now() - started >= 850)
  assert.ok(performance.now() - started < 3000)
  const boundary = createDshPetPilot({ ...config(server.baseUrl), observeMs: 5000, requestTimeoutMs: 1000, leaseMs: 9000 })
  assert.equal(boundary.status().enabled, true)
  await boundary.close()
})

test('preaborted signals and repeated idle cancellation have zero traffic', async t => {
  const { pilot, server } = await fixture(t)
  const controller = new AbortController(); controller.abort()
  assert.equal((await pilot.present('greeting', { signal: controller.signal })).code, 'cancelled')
  assert.equal((await pilot.cancel()).code, 'idle')
  assert.equal((await pilot.cancel()).animationStopConfirmed, false)
  assert.equal(server.requests.length, 0)
})

test('unconfirmed cleanup is not success and release failure blocks further traffic', async t => {
  const { pilot, server } = await fixture(t, { onRequest(entry, { send }) {
    if (route(entry) !== 'lock/release') return false
    send({ ok: false, reason: 'lock-lost', cause: 'preempted', private: 'DO_NOT_ECHO' }); return true
  } })
  const report = await pilot.present()
  assert.equal(report.code, 'cleanup-unconfirmed'); assert.equal(report.ok, false)
  assert.equal(report.cleanup.lock, 'lock-lost')
  const count = server.requests.length
  assert.equal((await pilot.present()).code, 'lease-cooldown')
  assert.equal(server.requests.length, count)
  assert.equal(JSON.stringify(report).includes('DO_NOT_ECHO'), false)
})

test('CLI mock Ctrl+C waits for adapter cleanup and removes its signal handler', async () => {
  const before = process.listenerCount('SIGINT')
  const timer = setTimeout(() => process.emit('SIGINT'), 100)
  try { assert.equal(await main(['--mock', '--scene', 'quiet']), 130) }
  finally { clearTimeout(timer) }
  assert.equal(process.listenerCount('SIGINT'), before)
})

test('unknown legacy playback statuses and mismatched IDs are rejected without raw echo', async t => {
  for (const patch of [{ status: 'started' }, { status: 'error' }, { status: 'PRIVATE_STATUS' }, { petId: 'private-other-pet' }, { playbackId: 'private-other-playback' }]) {
    const { pilot } = await fixture(t, { onRequest(entry, { send }) {
      if (!route(entry).startsWith('playback/')) return false
      send({ playbackId: 'fixture-playback-private', petId: 'yunlan-pilot', source: 'api', status: 'ended', createdAt: Date.now(), updatedAt: Date.now(), matched: false, ...patch }); return true
    } })
    const report = await pilot.present()
    assert.equal(report.ok, false); assert.equal(report.code, 'invalid-response')
    assert.equal(report.cleanup.lock, 'release-host-accepted')
    assert.equal(JSON.stringify(report).includes('private-other'), false)
    assert.equal(JSON.stringify(report).includes('PRIVATE_STATUS'), false)
  }
})

test('cancel during cleanup waits for both bounded cleanup calls', async t => {
  const seen = deferred()
  const { pilot, server } = await fixture(t, { onRequest(entry, { send }) {
    if (route(entry) !== 'bubble/clear') return false
    seen.resolve(); send({ ok: true }, { delayMs: 60 }); return true
  } })
  const running = pilot.present()
  await seen.promise
  const report = await pilot.cancel()
  assert.equal(report.code, 'cancelled'); assert.equal(report.ok, false)
  assert.deepEqual(await running, report)
  assert.equal(report.cleanup.bubble, 'clear-host-accepted')
  assert.equal(report.cleanup.lock, 'release-host-accepted')
  assert.equal(route(server.requests.at(-1)), 'lock/release')
})

test('mock close destroys sockets and is idempotent', async () => {
  const server = await createPetPilotServer()
  await server.close(); await server.close()
  assert.equal(server.socketCount, 0)
})
