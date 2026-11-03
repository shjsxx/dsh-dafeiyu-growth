import test from 'node:test'
import assert from 'node:assert/strict'
import { createGrowthApi, STATE_ENDPOINT, ACTION_ENDPOINT } from '../web/api.js'
const response = (body, ok = true) => ({ ok, json: async () => body })

test('state and confirmed reset preserve exact routes and request payload', async () => {
  const calls = []
  const api = createGrowthApi({ fetchImpl: async (path, options) => { calls.push([path, options]); return response(path === STATE_ENDPOINT ? { xp: 5 } : { ok: true, state: { xp: 0 } }) } })
  assert.deepEqual(await api.getState(), { xp: 5 })
  assert.deepEqual(await api.reset(), { xp: 0 })
  assert.equal(calls[0][0], STATE_ENDPOINT)
  assert.equal(calls[0][1].cache, 'no-store')
  assert.equal(calls[1][0], ACTION_ENDPOINT)
  assert.equal(calls[1][1].method, 'POST')
  assert.deepEqual(JSON.parse(calls[1][1].body), { type: 'reset' })
})
test('reset HTTP failures and ok:false are never success and never retried', async () => {
  for (const reply of [response({ ok: true, state: {} }, false), response({ ok: false, error: 'private-path' }), response({ ok: true }), response({ ok: true, state: [] }), response({ok:true,state:{}})]) {
    let calls = 0
    const api = createGrowthApi({ fetchImpl: async () => { calls++; return reply } })
    await assert.rejects(api.reset(), (error) => !error.message.includes('private-path'))
    assert.equal(calls, 1)
  }
})
test('malformed state JSON and raw network errors get safe messages', async () => {
  for (const fetchImpl of [async () => response([]), async () => response({}), async () => response({xp:'bad'}), async () => response({ ok: false }), async () => ({ ok: true, json() { throw Error('secret path') } }), async () => { throw Error('secret path') }]) {
    await assert.rejects(createGrowthApi({ fetchImpl }).getState(), (error) => !error.message.includes('secret'))
  }
})
test('timeout aborts and settles even when injected fetch ignores its signal', async () => {
  let signal
  const api = createGrowthApi({ timeoutMs: 5, fetchImpl: (_path, options) => { signal = options.signal; return new Promise(() => {}) } })
  await assert.rejects(api.getState(), { code: 'timeout' })
  assert.equal(signal.aborted, true)
})
test('caller abort cancels requests, including already-aborted signals', async () => {
  const abort = new AbortController()
  let calls = 0
  const api = createGrowthApi({ fetchImpl: async () => { calls++; return new Promise(() => {}) } })
  const pending = api.getState({ signal: abort.signal })
  abort.abort()
  await assert.rejects(pending, { code: 'aborted' })
  await assert.rejects(api.reset({ signal: abort.signal }), { code: 'aborted' })
  assert.equal(calls, 1)
})
