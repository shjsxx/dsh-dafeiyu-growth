// Optional stage-1 presentation boundary. No host registration, credentials, profile IO or LLM calls.
import http from 'node:http'

export const DSH_PET_CONTRACT = Object.freeze({
  version: '0.4.1', commit: '3c99b1bb4d1bcb831880e8871718c50b64af0d9b', prefix: '/dsh-pet-7340',
})
// Only fixed synthetic text is allowed in this pilot. Real core output requires a later consent/provenance bridge.
export const PET_DEMO_SCENES = Object.freeze({
  greeting: Object.freeze({ text: '你好，我是云岚。这是隔离合成演示，不读取真实记忆。', intent: 'greet' }),
  celebrate: Object.freeze({ text: '合成演示：完成了一个小目标。这里没有真实成长数据。', intent: 'celebrate' }),
  quiet: Object.freeze({ text: '合成演示：我会安静陪伴。此操作不会更改真实偏好。', intent: null }),
})
const REASONS = new Set(['locked', 'lock-lost', 'lock-required', 'unknown-pet', 'pet-required',
  'pet-not-displayed', 'bad-request', 'unknown-animation', 'unknown-playback', 'generate-error'])
const CAUSES = new Set(['preempted', 'expired', 'released', 'unknown'])
const STATUSES = new Set(['accepted', 'playing', 'ended', 'cancelled', 'failed'])
const TERMINAL = new Set(['ended', 'cancelled', 'failed'])
class PilotError extends Error {
  constructor(code, cause) { super(code); this.code = code; this.causeCode = cause }
}
const failure = (code, cause) => ({ ok: false, code, ...(CAUSES.has(cause) ? { cause } : {}) })
function business(body) {
  if (body?.ok === false) throw new PilotError(REASONS.has(body.reason) ? body.reason : 'upstream-rejected',
    CAUSES.has(body.cause) ? body.cause : undefined)
}
function identifier(value) { return typeof value === 'string' && /^[a-zA-Z0-9_-]{1,100}$/.test(value) }
function endpoint(value) {
  // Numeric IPv4 only: no DNS, credentials, URL normalization tricks, proxy settings or query/fragment.
  if (typeof value !== 'string' || !/^http:\/\/127\.0\.0\.1:[1-9]\d{0,4}$/.test(value)) throw new PilotError('invalid-endpoint')
  const port = Number(value.slice(value.lastIndexOf(':') + 1))
  if (port > 65535) throw new PilotError('invalid-endpoint')
  return { hostname: '127.0.0.1', port }
}
function boundedInteger(value, min, max) { return Number.isInteger(value) && value >= min && value <= max }

// Deliberately not exported as a general-purpose HTTP proxy. Routes are chosen below, never by external input.
function requestJson(target, method, path, body, signal, timeoutMs) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new PilotError('cancelled')); return }
    let settled = false
    let timer
    let req
    const finish = (error, data) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
      if (error) { req?.destroy(); reject(error) } else resolve(data)
    }
    const onAbort = () => finish(new PilotError('cancelled'))
    const payload = body === undefined ? undefined : Buffer.from(JSON.stringify(body))
    req = http.request({ ...target, path, method, agent: false, headers: {
      accept: 'application/json', 'cache-control': 'no-store',
      ...(payload ? { 'content-type': 'application/json', 'content-length': payload.length } : {}),
    } }, (res) => {
      // No redirects, cookies, auth challenges, or HTML login handling.
      if (res.statusCode !== 200) { finish(new PilotError('http-error')); res.destroy(); return }
      if (!/^application\/json(?:\s*;|$)/i.test(String(res.headers['content-type'] ?? ''))) {
        finish(new PilotError('invalid-response')); res.destroy(); return
      }
      const chunks = []
      let length = 0
      res.on('data', chunk => {
        length += chunk.length
        if (length > 16384) { finish(new PilotError('response-too-large')); res.destroy(); return }
        chunks.push(chunk)
      })
      res.on('aborted', () => finish(new PilotError('network-error')))
      res.on('error', () => finish(new PilotError('network-error')))
      res.on('end', () => {
        if (settled) return
        try {
          const data = JSON.parse(Buffer.concat(chunks).toString('utf8'))
          if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error()
          finish(undefined, data)
        } catch { finish(new PilotError('invalid-response')) }
      })
    })
    req.on('error', () => finish(new PilotError('network-error')))
    signal?.addEventListener('abort', onAbort, { once: true })
    timer = setTimeout(() => finish(new PilotError('timeout')), timeoutMs)
    req.end(payload)
    if (signal?.aborted) onAbort()
  })
}

function pause(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(new PilotError('cancelled')); return }
    const finish = error => {
      clearTimeout(timer)
      signal.removeEventListener('abort', onAbort)
      if (error) reject(error); else resolve()
    }
    const onAbort = () => finish(new PilotError('cancelled'))
    const timer = setTimeout(() => finish(), ms)
    signal.addEventListener('abort', onAbort, { once: true })
    if (signal.aborted) onAbort()
  })
}

/**
 * No activity until explicitly enabled AND present(scene) is called.
 * `acknowledgeUnauthenticatedLocalApi` acknowledges upstream risk; it does NOT add upstream authentication.
 * `surface=both` intentionally omits voiceTo upstream, preventing automatic TTS for this broadcast.
 * It requires a dedicated test pet configured display=both. Surface selects eligibility, not output isolation.
 */
export function createDshPetPilot(options = {}) {
  const enabled = options.enabled === true
  let configError
  let target
  let animations
  const petId = options.petId
  const requestTimeoutMs = options.requestTimeoutMs ?? 1000
  const leaseMs = options.leaseMs ?? 15000
  const observeMs = options.observeMs ?? 5000
  const cooldownMs = leaseMs + 2 * requestTimeoutMs
  try {
    if (!enabled) throw new PilotError('disabled')
    if (options.acknowledgeUnauthenticatedLocalApi !== true) throw new PilotError('local-api-risk-not-acknowledged')
    if (options.contractCommit !== DSH_PET_CONTRACT.commit) throw new PilotError('unconfirmed-contract')
    target = endpoint(options.baseUrl)
    if (typeof petId !== 'string' || !/^[a-zA-Z0-9_-]{1,64}$/.test(petId) || petId === 'main') throw new PilotError('dedicated-test-pet-required')
    if (!boundedInteger(requestTimeoutMs, 50, 3000) || !boundedInteger(leaseMs, 5000, 30000) ||
        !boundedInteger(observeMs, 0, 20000) || leaseMs < observeMs + 4 * requestTimeoutMs) throw new PilotError('invalid-time-budget')
    animations = Object.create(null)
    if (options.animations !== undefined && (!options.animations || typeof options.animations !== 'object' || Array.isArray(options.animations))) throw new PilotError('invalid-animation-map')
    for (const [intent, name] of Object.entries(options.animations ?? {})) {
      if (!['greet', 'celebrate'].includes(intent) || typeof name !== 'string' || !name.trim() ||
          name.length > 80 || /[\x00-\x1f\x7f/\\:?&#]/.test(name) || name.includes('..')) throw new PilotError('invalid-animation-map')
      animations[intent] = name
    }
  } catch (error) { configError = error.code ?? 'invalid-config' }
  let active
  let blockedUntil = 0
  let closed = false
  const query = (lockId) => new URLSearchParams({ pet: petId, surface: 'both', ...(lockId ? { lock: lockId } : {}) })
  const call = async (method, route, body, signal, lockId) => {
    const data = await requestJson(target, method, `${DSH_PET_CONTRACT.prefix}/${route}?${query(lockId)}`, body, signal, requestTimeoutMs)
    business(data)
    return data
  }
  const ack = data => { if (data.ok !== true) throw new PilotError('invalid-response'); return data }

  async function execute(session, scene) {
    let lockId
    let acquireAttempted = false
    let primary
    const report = { ok: false, code: 'not-started', playback: 'not-requested', bubble: 'not-requested',
      cleanup: { bubble: 'not-needed', lock: 'not-acquired' }, animationStopConfirmed: false }
    try {
      if (session.abort.signal.aborted) throw new PilotError('cancelled')
      acquireAttempted = true
      const lock = ack(await call('POST', 'lock', { ttl: leaseMs }, session.abort.signal))
      if (identifier(lock.lockId)) lockId = lock.lockId // Retain a usable ID even if other fields are malformed, for cleanup.
      if (!lockId || lock.petId !== petId || !Number.isSafeInteger(lock.expiresAt) ||
          lock.expiresAt <= Date.now() || lock.expiresAt > Date.now() + cooldownMs) throw new PilotError('invalid-response')
      const bubble = await call('POST', 'broadcast', { text: scene.text, anim: false, duration: Math.min(observeMs || 3000, 10000) }, session.abort.signal, lockId)
      ack(bubble)
      report.bubble = 'host-accepted'
      const name = scene.intent && animations[scene.intent]
      let playbackId
      if (name) {
        const animation = ack(await call('POST', 'anim', { name }, session.abort.signal, lockId))
        if (!identifier(animation.playbackId)) throw new PilotError('invalid-response')
        playbackId = animation.playbackId
        report.playback = 'accepted'
      }
      // Keep a bubble-only scene visible too; immediate clear can be coalesced away by the renderer.
      const startedAt = performance.now()
      while (performance.now() - startedAt < observeMs) {
        // Renew with the SAME ID to detect user takeover. Never reacquire or force release.
        const renewal = ack(await call('POST', 'lock', { lockId, ttl: leaseMs }, session.abort.signal))
        if (renewal.lockId !== lockId || renewal.petId !== petId || !Number.isSafeInteger(renewal.expiresAt) ||
            renewal.expiresAt <= Date.now() || renewal.expiresAt > Date.now() + cooldownMs) throw new PilotError('invalid-response')
        if (playbackId) {
          const receipt = await call('GET', `playback/${playbackId}`, undefined, session.abort.signal)
          if (receipt.playbackId !== playbackId || receipt.petId !== petId || !STATUSES.has(receipt.status)) throw new PilotError('invalid-response')
          report.playback = receipt.status
          if (TERMINAL.has(receipt.status)) break
        }
        await pause(Math.min(250, Math.max(0, observeMs - (performance.now() - startedAt))), session.abort.signal)
      }
      if (session.abort.signal.aborted) throw new PilotError('cancelled')
      report.ok = !name || report.playback === 'ended'
      report.code = report.playback === 'ended' ? 'renderer-reported-ended' :
        report.playback === 'failed' ? 'renderer-reported-failed' :
          report.playback === 'cancelled' ? 'renderer-reported-cancelled' : name ? 'playback-unconfirmed' : 'bubble-host-accepted'
    } catch (error) {
      primary = failure(error.code ?? 'internal-error', error.causeCode)
      Object.assign(report, primary)
    } finally {
      // A cancelled client request may already have executed upstream. Cleanup is bounded and never claims a stop.
      if (lockId) {
        try { ack(await call('POST', 'bubble/clear', undefined, undefined, lockId)); report.cleanup.bubble = 'clear-host-accepted' }
        catch (error) { report.cleanup.bubble = error.code ?? 'cleanup-failed' }
        try { ack(await call('POST', 'lock/release', { lockId }, undefined)); report.cleanup.lock = 'release-host-accepted' }
        catch (error) { report.cleanup.lock = error.code ?? 'cleanup-failed' }
      } else if (acquireAttempted && !['locked', 'unknown-pet', 'pet-not-displayed', 'bad-request', 'pet-required'].includes(primary?.code)) {
        report.cleanup.lock = 'unknown-wait-for-lease'
        blockedUntil = Date.now() + cooldownMs
      }
      if (lockId && report.cleanup.lock !== 'release-host-accepted') blockedUntil = Date.now() + cooldownMs
      if (lockId && (report.cleanup.lock !== 'release-host-accepted' ||
          report.cleanup.bubble !== 'clear-host-accepted')) {
        report.ok = false
        if (!primary) report.code = 'cleanup-unconfirmed'
      }
    }
    if (session.abort.signal.aborted) { report.ok = false; report.code = 'cancelled' }
    // No returned text, endpoint, raw upstream error, memory, lock ID or playback ID.
    return report
  }

  return Object.freeze({
    status: () => Object.freeze({ enabled: enabled && !configError && !closed, busy: Boolean(active),
      reason: closed ? 'closed' : configError ?? (Date.now() < blockedUntil ? 'lease-cooldown' : 'ready'), syntheticOnly: true }),
    present(sceneId = 'greeting', { signal } = {}) {
      if (closed || configError) return Promise.resolve(failure(closed ? 'closed' : configError))
      if (active) return Promise.resolve(failure('busy'))
      if (Date.now() < blockedUntil) return Promise.resolve(failure('lease-cooldown'))
      if (typeof sceneId !== 'string' || !Object.hasOwn(PET_DEMO_SCENES, sceneId)) return Promise.resolve(failure('synthetic-scene-required'))
      if (signal?.aborted) return Promise.resolve(failure('cancelled'))
      const session = { abort: new AbortController() }
      active = session
      const abort = () => session.abort.abort()
      signal?.addEventListener('abort', abort, { once: true })
      session.done = execute(session, PET_DEMO_SCENES[sceneId]).finally(() => {
        signal?.removeEventListener('abort', abort)
        if (active === session) active = undefined
      })
      return session.done
    },
    async cancel() {
      if (!active) return { ok: true, code: 'idle', animationStopConfirmed: false }
      const session = active
      session.abort.abort()
      return session.done
    },
    async close() { closed = true; if (active) { active.abort.abort(); return active.done } return { ok: true, code: 'closed' } },
  })
}
