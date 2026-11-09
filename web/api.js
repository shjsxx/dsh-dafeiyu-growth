export const STATE_ENDPOINT = '/plugins/dsh-dafeiyu-growth/state'
export const ACTION_ENDPOINT = '/plugins/dsh-dafeiyu-growth/action'
const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value)
const isGrowth = value => isRecord(value) && typeof value.xp === 'number' && Number.isFinite(value.xp) && value.xp >= 0
export class GrowthApiError extends Error {
  constructor(code, message) { super(message); this.name = 'GrowthApiError'; this.code = code }
}
export function createGrowthApi({ fetchImpl = (...args) => globalThis.fetch(...args), timeoutMs = 10000 } = {}) {
  async function request(path, { signal, body: payload = null } = {}) {
    const action = payload !== null
    const controller = new AbortController()
    let timedOut = false
    let timer
    const abort = () => controller.abort()
    const aborted = new Promise((_, reject) => {
      controller.signal.addEventListener('abort', () => reject(new GrowthApiError(timedOut ? 'timeout' : 'aborted', timedOut ? '请求超时，请稍后手动重试。' : '请求已取消。')), { once: true })
    })
    signal?.addEventListener('abort', abort, { once: true })
    if (signal?.aborted) abort()
    if (!controller.signal.aborted) timer = setTimeout(() => { timedOut = true; abort() }, Math.max(1, Number.isFinite(timeoutMs) ? timeoutMs : 10000))
    try {
      const work = async () => {
        if (controller.signal.aborted) return await aborted
        const response = await fetchImpl(path, { method: action ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
          ...(action ? { headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload) } : {}) })
        if (!response?.ok) throw new GrowthApiError('http', action ? '重置未成功，请检查本地服务后重试。' : '暂时无法读取成长档案。')
        let body
        try { body = await response.json() } catch { throw new GrowthApiError('invalid', '服务返回了无法识别的数据。') }
        if (!isRecord(body) || (action && (body.ok !== true || !isGrowth(body.state))) || (!action && (body.ok === false || !isGrowth(body)))) throw new GrowthApiError('invalid', action ? '写入未成功确认，请刷新后核对。' : '服务返回了无法识别的数据。')
        return action ? body.state : body
      }
      return await Promise.race([work(), aborted])
    } catch (error) {
      if (error instanceof GrowthApiError) throw error
      if (controller.signal.aborted) throw new GrowthApiError(timedOut ? 'timeout' : 'aborted', timedOut ? '请求超时，请稍后手动重试。' : '请求已取消。')
      throw new GrowthApiError('network', '无法连接本地成长服务，请稍后重试。')
    } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort) }
  }
  return {
    getState: ({ signal } = {}) => request(STATE_ENDPOINT, { signal }),
    reset: ({ signal } = {}) => request(ACTION_ENDPOINT, { signal, body: { type: 'reset' } }),
    addDiary: (text, { signal } = {}) => request(ACTION_ENDPOINT, { signal, body: { type: 'diary', text } }),
  }
}
