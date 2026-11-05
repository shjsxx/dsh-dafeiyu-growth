// DSH host-side entry for dsh-dafeiyu-growth.
//
// Subscribes to the shared session event stream, folds each event into a
// persisted growth profile, and exposes it over two loopback HTTP routes:
//
//   GET  /plugins/dsh-dafeiyu-growth/state   → derived growth view
//   POST /plugins/dsh-dafeiyu-growth/action  → { type: 'diary' | 'reset' }
//
// A queue file lets the CLI (tools/growth.mjs) write diary entries while the
// host is running, without having to discover the random WebUI port.
import { existsSync, readFileSync, renameSync, unlinkSync } from 'node:fs'
import { createHash } from 'node:crypto'
import {
  addDiary, derive, levelFromXp, loadProfile, observe, resetProfile, stageOf,
} from './growth.js'
import { createStore, defaultProfilePath, queuePathForProfile } from './store.js'
import { NativeSupervisor, pluginRootFrom } from './native-supervisor.js'

const BASE = '/plugins/dsh-dafeiyu-growth'
export const STATE_ENDPOINT = `${BASE}/state`
export const ACTION_ENDPOINT = `${BASE}/action`

const MAX_BODY = 64 * 1024

function json(res, code, body) {
  const text = JSON.stringify(body)
  res.statusCode = code
  res.setHeader('content-type', 'application/json; charset=utf-8')
  res.setHeader('cache-control', 'no-store')
  res.end(text)
}

function isLoopback(req) {
  const address = req?.socket?.remoteAddress ?? ''
  const host = String(req?.headers?.host ?? '')
  const localAddress = address === '127.0.0.1' || address === '::1' || address === '::ffff:127.0.0.1'
  const localHost = host === '' || /^(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$/i.test(host)
  return localAddress && localHost
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0
    const chunks = []
    req.on('data', (chunk) => {
      size += chunk.length
      if (size > MAX_BODY) {
        reject(new Error('body too large'))
        req.destroy()
        return
      }
      chunks.push(chunk)
    })
    req.on('end', () => {
      try {
        resolve(chunks.length ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : {})
      } catch (error) {
        reject(new Error(`invalid JSON body: ${error.message}`))
      }
    })
    req.on('error', reject)
  })
}

// Loopback alone is not CSRF protection: websites can POST to localhost.
function isSameOrigin(req) {
  if (req.headers?.['sec-fetch-site'] === 'cross-site') return false
  const origin = req.headers?.origin
  if (!origin) return true // non-browser local clients
  try {
    const url = new URL(origin)
    return (url.protocol === 'http:' || url.protocol === 'https:') &&
      url.host.toLowerCase() === String(req.headers?.host ?? '').toLowerCase()
  } catch { return false }
}

export async function apply(ctx, config = {}) {
  if (config.enabled === false) return
  const profilePath = defaultProfilePath()
  const queuePath = queuePathForProfile(profilePath)
  const store = createStore(profilePath, { warn: (message) => ctx.logger?.warn?.(message) })

  let profile = loadProfile(store.get())
  const turnStarts = new Map()
  const disposers = []
  const nativeSupervisor = new NativeSupervisor({
    root: pluginRootFrom(import.meta.url),
    config,
    logger: ctx.logger ?? console,
  })
  nativeSupervisor.start()

  const reset = () => {
    store.set(profile)
    if (!store.backup()) throw new Error('reset backup failed; profile preserved')
    const { queueBatch, recentEvents, queueRecords } = profile
    profile = resetProfile()
    // Reset statistics, not acknowledgement watermarks: an old delivery must
    // not resurrect pre-reset activity if a producer retries it.
    profile.queueBatch = queueBatch
    profile.recentEvents = recentEvents ?? []
    profile.queueRecords = queueRecords ?? []
    turnStarts.clear()
  }

  const applyRecord = (record) => {
    if (!record || typeof record !== 'object') return false
    const id = typeof record.id === 'string' ? record.id : undefined
    if (id && profile.queueRecords?.includes(id)) return false
    let applied = false
    if (record.type === 'diary') {
      const at = Date.parse(record.at)
      applied = typeof record.text === 'string' && addDiary(profile, record.text, Number.isFinite(at) ? at : Date.now())
    } else if (record.type === 'reset' && record.confirm === true) {
      reset()
      applied = true
    }
    if (applied && id) profile.queueRecords = [...(profile.queueRecords ?? []), id].slice(-2048)
    return applied
  }

  const ingestExternal = () => {
    // Claim a batch by rename. Producers append to a new live queue, never a
    // file we later truncate. Keep the claimed batch until its commit succeeds.
    const pending = `${queuePath}.processing`
    let snapshot
    const starts = new Map(turnStarts)
    try {
      if (!existsSync(pending)) {
        if (!existsSync(queuePath)) return 0
        renameSync(queuePath, pending)
      }
      const text = readFileSync(pending, 'utf8')
      if (text && !text.endsWith('\n')) return 0 // writer may still hold an open descriptor
      const batch = createHash('sha256').update(text).digest('hex')
      if (profile.queueBatch === batch) { unlinkSync(pending); return 0 }
      snapshot = JSON.parse(JSON.stringify(profile))
      let applied = 0
      for (const line of text.split(/\r?\n/)) {
        if (!line.trim()) continue
        let record
        try { record = JSON.parse(line) } catch {
          ctx.logger?.warn?.('[dsh-dafeiyu-growth] ignored malformed queue line')
          continue
        }
        if (applyRecord(record)) applied += 1
      }
      profile.queueBatch = batch
      store.set(profile)
      if (!store.save({ immediate: true })) throw new Error('queue commit failed')
      snapshot = undefined // committed: a failed unlink must not roll it back
      unlinkSync(pending)
      return applied
    } catch (error) {
      if (snapshot) {
        profile = loadProfile(snapshot)
        store.set(profile)
        turnStarts.clear()
        for (const [key, at] of starts) turnStarts.set(key, at)
      }
      ctx.logger?.warn?.(`[dsh-dafeiyu-growth] queue retained: ${error.message}`)
      return 0
    }
  }

  const keyOf = (session) => String(session?.id ?? session?.sessionId ?? session?.key ?? 'session')

  const onEvent = (session, event) => {
    try {
      const key = keyOf(session)
      const type = event?.type
      ingestExternal()
      const turnStart = turnStarts.get(key)
      const beforeLevel = levelFromXp(profile.xp)
      const beforeStage = stageOf(beforeLevel).key
      const result = observe(profile, { event, session, now: Date.now(), turnStart })
      if (!result.accepted) return
      if (type === 'turn/start') turnStarts.set(key, Date.now())
      if (type === 'turn/end') turnStarts.delete(key)
      const afterLevel = levelFromXp(profile.xp)
      const afterStage = stageOf(afterLevel)
      if (afterStage.key !== beforeStage) {
        addDiary(profile, `我进化了：${afterStage.name} · ${afterStage.title}`, Date.now(), { kind: 'stage' })
        ctx.logger?.info?.(`[dsh-dafeiyu-growth] stage up → ${afterStage.name} (Lv.${afterLevel})`)
      } else if (afterLevel > beforeLevel) {
        ctx.logger?.info?.(`[dsh-dafeiyu-growth] level up → Lv.${afterLevel}`)
      }
      store.set(profile)
      store.save()
    } catch (error) {
      ctx.logger?.warn?.(`[dsh-dafeiyu-growth] failed to record session event: ${error?.message ?? error}`)
    }
  }

  const onDisposed = (session) => {
    turnStarts.delete(keyOf(session))
  }

  try {
    disposers.push(ctx.on('session/event', onEvent) ?? (() => {}))
    disposers.push(ctx.on('session/disposed', onDisposed) ?? (() => {}))
  } catch (error) {
    ctx.logger?.warn?.(`[dsh-dafeiyu-growth] session events unavailable: ${error?.message ?? error}`)
  }

  const state = () => ({ ...derive(profile), storageHealth: store.health() })

  const stateHandler = (req, res) => {
    if (!isLoopback(req) || !isSameOrigin(req)) return json(res, 403, { ok: false, error: 'local same-origin only' })
    if (req.method !== 'GET') return json(res, 405, { ok: false, error: 'GET only' })
    ingestExternal()
    return json(res, 200, state())
  }

  const actionHandler = async (req, res) => {
    if (!isLoopback(req) || !isSameOrigin(req)) return json(res, 403, { ok: false, error: 'local same-origin only' })
    if (req.method !== 'POST') return json(res, 405, { ok: false, error: 'POST only' })
    if (!/^application\/json(?:\s*;|$)/i.test(String(req.headers?.['content-type'] ?? ''))) {
      return json(res, 415, { ok: false, error: 'application/json required' })
    }
    let body
    try {
      body = await readBody(req)
    } catch (error) {
      return json(res, 400, { ok: false, error: String(error?.message ?? error) })
    }
    ingestExternal()
    const snapshot = JSON.parse(JSON.stringify(profile))
    const starts = new Map(turnStarts)
    try {
      if (body?.type === 'diary' && typeof body.text === 'string' && addDiary(profile, body.text)) {
        // handled below
      } else if (body?.type === 'reset') {
        reset()
      } else {
        return json(res, 400, { ok: false, error: 'unknown action' })
      }
      store.set(profile)
      if (!store.save({ immediate: true })) throw new Error('profile save failed')
      return json(res, 200, { ok: true, state: state() })
    } catch (error) {
      profile = loadProfile(snapshot)
      turnStarts.clear()
      for (const [key, at] of starts) turnStarts.set(key, at)
      store.set(profile)
      return json(res, 503, { ok: false, error: String(error.message) })
    }
  }

  try {
    disposers.push(ctx.webServer.register({ kind: 'exact', path: STATE_ENDPOINT, handler: stateHandler }))
    disposers.push(ctx.webServer.register({ kind: 'exact', path: ACTION_ENDPOINT, handler: actionHandler }))
  } catch (error) {
    ctx.logger?.warn?.(`[dsh-dafeiyu-growth] failed to register routes: ${error?.message ?? error}`)
  }

  ingestExternal()
  const queueTimer = setInterval(ingestExternal, 1000)
  queueTimer.unref?.()
  disposers.push(() => clearInterval(queueTimer))

  consoleLogSafely(`[dsh-dafeiyu-growth] ready · Lv.${levelFromXp(profile.xp)}`)

  ctx.effect(() => () => {
    try { nativeSupervisor.stop() } catch {}
    for (const dispose of disposers) {
      try { dispose?.() } catch {}
    }
    store.flush()
    store.dispose()
  })
}

function consoleLogSafely(message) {
  try { console.log(message) } catch {}
}

export const name = 'dsh-dafeiyu-growth'
export const inject = ['webServer', 'sessions']
