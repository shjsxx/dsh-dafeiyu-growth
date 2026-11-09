import { useEffect, useRef, useState } from 'react'
import { createGrowthApi } from './api.js'
import { createDashboardModel } from './model.js'

const safeError = (error, action) => action ? '清零尚未获成功确认，请先刷新核对；不要连续重复提交。' : error?.code === 'timeout' ? '请求超时，请稍后重试。' : '暂时无法读取档案；已有记录会保留显示。'

// External-store controller is exported so lifecycle, visibility, and races can
// be exercised offline without coupling the tests to a React renderer.
export function createGrowthController({ api = createGrowthApi(), intervalMs = 15000, timeoutMs = 10000,
  documentImpl = typeof document === 'undefined' ? null : document, now = () => Date.now(),
  setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
  let snapshot = { state: null, loading: true, refreshing: false, busy: false, error: null, actionError: null, lastUpdated: null }
  let active = false
  let generation = 0
  let timer = null
  let operation = null
  const listeners = new Set()
  const emit = (patch) => { if (!active) return; snapshot = { ...snapshot, ...patch }; for (const listener of listeners) listener() }
  const visible = () => documentImpl?.visibilityState !== 'hidden'
  const clearPoll = () => { if (timer !== null) clearTimer(timer); timer = null }
  const schedule = () => {
    clearPoll()
    if (!active || !visible() || snapshot.busy) return
    timer = setTimer(() => { timer = null; void refresh() }, Math.max(100, Number.isFinite(intervalMs) ? intervalMs : 15000))
  }
  const invalidate = () => { generation += 1; if (operation) { clearTimer(operation.deadline); operation.abort.abort() }; operation = null; clearPoll() }
  async function perform(kind, payload) {
    if (!active) return false
    if (kind === 'refresh' && (!visible() || snapshot.busy)) return false
    if (kind !== 'refresh' && snapshot.busy) return false
    if (kind === 'refresh' && operation) return operation.promise
    if (kind === 'reset' || kind === 'diary') invalidate()
    clearPoll()
    const token = ++generation
    const abort = new AbortController()
    const current = { abort, promise: null }
    operation = current
    emit(kind === 'refresh' ? { loading: snapshot.state === null, refreshing: snapshot.state !== null, error: null }
      : { busy: true, refreshing: false, actionError: null })
    current.promise = (async () => {
      let deadline
      const cancelled = new Promise((_, reject) => {
        abort.signal.addEventListener('abort', () => reject({ code: 'aborted' }), { once: true })
        deadline = setTimer(() => { reject({ code: 'timeout' }); abort.abort() }, Math.max(1, Number.isFinite(timeoutMs) ? timeoutMs : 10000))
        current.deadline = deadline
      })
      try {
        const request = kind === 'refresh'
          ? Promise.resolve().then(() => {
              if (abort.signal.aborted) throw { code: 'aborted' }
              return api.getState({ signal: abort.signal })
            })
          : kind === 'reset'
            ? api.reset({ signal: abort.signal })
            : api.addDiary(payload, { signal: abort.signal })
        const raw = await Promise.race([request, cancelled])
        if (!active || token !== generation) return false
        emit({ state: createDashboardModel(raw, { now: now() }), lastUpdated: now(), error: null, ...(kind === 'refresh' ? {} : { actionError: null }) })
        return true
      } catch (error) {
        if (!active || token !== generation) return false
        if (error?.code !== 'aborted') emit(kind === 'refresh' ? { error: safeError(error, false) } : { actionError: kind === 'reset' ? safeError(error, true) : '回顾未保存；请检查本地服务后重试。' })
        return false
      } finally {
        clearTimer(deadline)
        if (active && token === generation) {
          operation = null
          emit({ loading: false, refreshing: false, busy: false })
          schedule()
        }
      }
    })()
    return current.promise
  }
  function refresh() { return perform('refresh') }
  function reset() { return perform('reset') }
  function addDiary(text) { return perform('diary', text) }
  const onVisibility = () => {
    clearPoll()
    if (!active) return
    if (!visible()) {
      if (!snapshot.busy && operation) { invalidate(); emit({ loading: false, refreshing: false }) }
    } else if (!snapshot.busy) void refresh()
  }
  return {
    getSnapshot: () => snapshot,
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener) },
    start() { if (active) return; active = true; documentImpl?.addEventListener?.('visibilitychange', onVisibility); if (visible()) void refresh() },
    stop() { active = false; invalidate(); snapshot = { ...snapshot, loading: snapshot.state === null, refreshing: false, busy: false }; documentImpl?.removeEventListener?.('visibilitychange', onVisibility) },
    refresh, reset, addDiary,
  }
}

export function useGrowthDashboard(options = {}) {
  const ref = useRef(null)
  if (ref.current === null) ref.current = createGrowthController(options)
  const controller = ref.current
  const [snapshot, setSnapshot] = useState(controller.getSnapshot)
  useEffect(() => {
    const unsubscribe = controller.subscribe(() => setSnapshot(controller.getSnapshot()))
    controller.start()
    setSnapshot(controller.getSnapshot())
    return () => { unsubscribe(); controller.stop() }
  }, [controller])
  return { ...snapshot, refresh: controller.refresh, reset: controller.reset, addDiary: controller.addDiary }
}
