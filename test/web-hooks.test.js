import test from 'node:test'
import assert from 'node:assert/strict'
import { createGrowthController, useGrowthDashboard } from '../web/hooks.js'

const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve() }
function fixture(api, options = {}) {
  let nextId = 0
  const timers = new Map()
  const visibilityListeners = new Set()
  const documentImpl = { visibilityState: 'visible', addEventListener: (_name, listener) => visibilityListeners.add(listener), removeEventListener: (_name, listener) => visibilityListeners.delete(listener) }
  const controller = createGrowthController({ api, documentImpl, now: () => 1000, setTimer: (fn, ms) => { const id = ++nextId; timers.set(id, { fn, ms }); return id }, clearTimer: (id) => timers.delete(id), ...options })
  return { controller, timers, visibilityListeners, documentImpl,
    tick(ms = 15000) { const found = [...timers].find(([, timer]) => timer.ms === ms); assert.ok(found, `expected timer ${ms}`); timers.delete(found[0]); found[1].fn() },
    async hide() { documentImpl.visibilityState = 'hidden'; for (const listener of visibilityListeners) listener(); await flush() },
    async show() { documentImpl.visibilityState = 'visible'; for (const listener of visibilityListeners) listener(); await flush() } }
}
test('initial loading, serial polling and retained stale state on read failure', async () => {
  const first = deferred(), second = deferred()
  let calls = 0
  const f = fixture({ getState: () => (++calls === 1 ? first.promise : second.promise) })
  assert.equal(f.controller.getSnapshot().loading, true)
  f.controller.start()
  await flush()
  assert.equal(calls, 1)
  const duplicate = f.controller.refresh()
  assert.equal(calls, 1)
  first.resolve({ xp: 25, counters: { events: 3 } })
  assert.equal(await duplicate, true)
  assert.equal(f.controller.getSnapshot().state.xp, 25)
  assert.equal(f.controller.getSnapshot().loading, false)
  f.tick()
  await flush()
  assert.equal(calls, 2)
  assert.equal(f.controller.getSnapshot().refreshing, true)
  assert.equal(f.timers.size, 1, 'only request timeout exists while poll is slow')
  second.reject(Error('private filesystem path'))
  await flush()
  assert.equal(f.controller.getSnapshot().state.xp, 25)
  assert.match(f.controller.getSnapshot().error, /无法读取/)
  assert.equal(f.controller.getSnapshot().refreshing, false)
  f.controller.stop()
  assert.equal(f.timers.size, 0)
})
test('reset invalidates slow poll, uses confirmed state and cannot be overwritten', async () => {
  const old = deferred(), action = deferred()
  let readSignal, actionSignal, reads = 0, resets = 0
  const f = fixture({ getState: ({ signal }) => { reads++; readSignal = signal; return old.promise }, reset: ({ signal }) => { resets++; actionSignal = signal; return action.promise } })
  f.controller.start(); await flush()
  const reset = f.controller.reset(); await flush()
  assert.equal(readSignal.aborted, true)
  assert.equal(f.controller.getSnapshot().busy, true)
  assert.equal(await f.controller.reset(), false)
  assert.equal(await f.controller.refresh(), false)
  assert.equal(resets, 1)
  action.resolve({ xp: 0 })
  assert.equal(await reset, true)
  assert.equal(actionSignal.aborted, false)
  old.resolve({ xp: 9000 }); await flush()
  assert.equal(f.controller.getSnapshot().state.xp, 0)
  assert.equal(reads, 1)
  assert.equal(f.controller.getSnapshot().busy, false)
  assert.equal(f.timers.size, 1)
  f.controller.stop()
})
test('failed reset keeps previous state and exposes actionError independently', async () => {
  const f = fixture({ getState: async () => ({ xp: 42 }), reset: async () => { throw Error('private path') } })
  f.controller.start(); await flush()
  assert.equal(await f.controller.reset(), false)
  const state = f.controller.getSnapshot()
  assert.equal(state.state.xp, 42)
  assert.equal(state.error, null)
  assert.match(state.actionError, /未获成功确认/)
  assert.doesNotMatch(state.actionError, /private/)
  f.controller.stop()
})
test('hidden tabs abort polls, pause timers and resume one request', async () => {
  const pending = deferred()
  let calls = 0, signal
  const f = fixture({ getState: (options) => { calls++; signal = options.signal; return calls === 1 ? pending.promise : Promise.resolve({ xp: 8 }) } })
  f.controller.start(); await flush()
  await f.hide()
  assert.equal(signal.aborted, true)
  assert.equal(f.timers.size, 0)
  assert.equal(await f.controller.refresh(), false)
  await f.show()
  assert.equal(calls, 2)
  assert.equal(f.controller.getSnapshot().state.xp, 8)
  pending.resolve({ xp: 500 }); await flush()
  assert.equal(f.controller.getSnapshot().state.xp, 8)
  f.controller.stop()
  assert.equal(f.visibilityListeners.size, 0)
})
test('stop aborts actions and prevents callbacks; strict-effect restart remains usable', async () => {
  const pending = deferred()
  let signal, notices = 0
  const f = fixture({ getState: async () => ({ xp: 3 }), reset: (options) => { signal = options.signal; return pending.promise } })
  f.controller.subscribe(() => notices++)
  f.controller.start(); await flush()
  const action = f.controller.reset(); await flush()
  const before = notices
  f.controller.stop()
  assert.equal(signal.aborted, true)
  assert.equal(f.timers.size, 0, 'unmount synchronously clears every timer')
  pending.resolve({ xp: 0 })
  assert.equal(await action, false)
  assert.equal(notices, before)
  assert.equal(f.timers.size, 0)
  f.controller.start(); await flush()
  assert.equal(f.controller.getSnapshot().busy, false)
  assert.equal(f.controller.getSnapshot().state.xp, 3)
  f.controller.stop()
})
test('timeout bounds injected requests that ignore abort and poll resumes later', async () => {
  let signal, calls = 0
  const f = fixture({ getState: (options) => { signal = options.signal; calls++; return new Promise(() => {}) } }, { timeoutMs: 20 })
  f.controller.start(); await flush()
  f.tick(20); await flush()
  assert.equal(signal.aborted, true)
  assert.equal(f.controller.getSnapshot().loading, false)
  assert.match(f.controller.getSnapshot().error, /超时/)
  assert.equal(f.timers.size, 1)
  f.tick(); await flush()
  assert.equal(calls, 2)
  f.controller.stop(); await flush()
  assert.equal(f.timers.size, 0)
})

test('React StrictMode hook integrates state, confirmed actions and unmount cleanup', async () => {
  const { JSDOM } = await import('jsdom')
  const React = await import('react')
  const { createRoot } = await import('react-dom/client')
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://localhost/' })
  const previousWindow = globalThis.window
  const previousDocument = globalThis.document
  const previousAct = globalThis.IS_REACT_ACT_ENVIRONMENT
  globalThis.window = dom.window
  globalThis.document = dom.window.document
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  let dashboard, reads = 0, actionSignal
  const action = deferred()
  const api = { getState: async () => { reads++; return { xp: 7, createdAt: '2026-01-01T00:00:00Z' } }, reset: ({ signal }) => { actionSignal = signal; return action.promise } }
  function Harness() { dashboard = useGrowthDashboard({ api, documentImpl: null }); return React.createElement('span', null, dashboard.loading ? 'loading' : dashboard.state?.xp) }
  const root = createRoot(dom.window.document.getElementById('root'))
  try {
    await React.act(async () => { root.render(React.createElement(React.StrictMode, null, React.createElement(Harness))); await flush() })
    assert.equal(reads, 1, 'StrictMode probe aborts before launching its obsolete request')
    assert.equal(dashboard.state.xp, 7)
    assert.equal(typeof dashboard.state.ageDays, 'number')
    assert.equal(dom.window.document.querySelector('span').textContent, '7')
    let pending
    await React.act(async () => { pending = dashboard.reset(); await flush() })
    assert.equal(dashboard.busy, true)
    await React.act(async () => root.unmount())
    assert.equal(actionSignal.aborted, true)
    action.resolve({ xp: 0 })
    assert.equal(await pending, false)
  } finally {
    await React.act(async () => root.unmount())
    dom.window.close()
    globalThis.window = previousWindow
    globalThis.document = previousDocument
    globalThis.IS_REACT_ACT_ENVIRONMENT = previousAct
  }
})
