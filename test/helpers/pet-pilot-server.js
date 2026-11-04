import http from 'node:http'

// Isolated in-memory fixture. Never connects to upstream or reads profiles/keys.
export async function createPetPilotServer(options = {}) {
  const requests = []
  const sockets = new Set()
  const timers = new Set()
  const petId = options.petId ?? 'yunlan-pilot'
  const lockId = 'fixture-lock-private'
  const playbackId = 'fixture-playback-private'
  let held = false
  let closed = false
  let playbackReads = 0
  const server = http.createServer(async (req, res) => {
    let raw = ''
    for await (const chunk of req) raw += chunk
    const url = new URL(req.url, 'http://127.0.0.1')
    let body
    try { body = raw ? JSON.parse(raw) : undefined } catch { res.writeHead(400).end(); return }
    const entry = { method: req.method, path: url.pathname, query: Object.fromEntries(url.searchParams), body }
    requests.push(entry)
    const send = (value, { status = 200, headers = {}, raw: rawBody, delayMs = 0 } = {}) => {
      const deliver = () => {
        if (closed || res.destroyed) return
        res.writeHead(status, { 'content-type': 'application/json', ...headers })
        res.end(rawBody ?? JSON.stringify(value))
      }
      if (delayMs) {
        const timer = setTimeout(() => { timers.delete(timer); deliver() }, delayMs)
        timers.add(timer)
      } else deliver()
    }
    if (options.onRequest && await options.onRequest(entry, { send, req, res, requests }) === true) return
    const route = url.pathname.replace('/dsh-pet-7340/', '')
    if (url.searchParams.get('pet') !== petId || url.searchParams.get('surface') !== 'both' || url.searchParams.has('voiceTo')) {
      send({ ok: false, reason: 'pet-not-displayed' }); return
    }
    if (req.method === 'POST' && route === 'lock') {
      if (options.contention && !body?.lockId) { send({ ok: false, reason: 'locked' }); return }
      if (body?.lockId && (body.lockId !== lockId || options.preempted)) {
        send({ ok: false, reason: 'lock-lost', cause: 'preempted' }); return
      }
      held = true
      send({ ok: true, lockId, petId, expiresAt: Date.now() + (body?.ttl ?? 15000) }, { delayMs: options.acquireDelayMs ?? 0 }); return
    }
    if (req.method === 'GET' && route === `playback/${playbackId}`) {
      const states = options.statuses ?? ['ended']
      const status = states[Math.min(playbackReads++, states.length - 1)]
      send({ playbackId, petId, source: 'api', status, createdAt: Date.now() - 10, updatedAt: Date.now(), matched: false }); return
    }
    if (req.method === 'POST' && route === 'lock/release') {
      if (body?.lockId !== lockId) { send({ ok: false, reason: 'lock-required' }); return }
      held = false; send({ ok: true }); return
    }
    if (req.method === 'POST' && ['broadcast', 'anim', 'bubble/clear'].includes(route)) {
      if (!held || url.searchParams.get('lock') !== lockId) { send({ ok: false, reason: 'lock-required' }); return }
      send(route === 'anim' ? { ok: true, playbackId } : { ok: true }); return
    }
    send({ ok: false, reason: 'bad-request' }, { status: 404 })
  })
  server.on('connection', socket => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)) })
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve) })
  return {
    baseUrl: `http://127.0.0.1:${server.address().port}`, requests,
    get socketCount() { return sockets.size },
    async close() {
      if (closed) return
      closed = true
      for (const timer of timers) clearTimeout(timer)
      timers.clear()
      const done = new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
      for (const socket of sockets) socket.destroy()
      await done
    },
  }
}
