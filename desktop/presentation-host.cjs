// Yunlan's restricted host for the real pinned dsh-pet renderer. No HTTP listener or model services.
const fs = require('node:fs');
const path = require('node:path');
const PIN = '3c99b1bb4d1bcb831880e8871718c50b64af0d9b';
const COMMANDS = new Set(['open-menu', 'open-conversation', 'open-growth', 'open-evolution', 'open-research', 'open-memory-curation', 'open-persona', 'open-voice-tools', 'open-cloud-settings', 'open-realtime-settings', 'emergency-stop', 'show', 'hide', 'feedback-positive', 'feedback-negative', 'interact-head', 'interact-poke', 'toggle-roam', 'toggle-reduced', 'toggle-chatter', 'spin', 'scale-up', 'scale-down']);
const STATES = new Set(['IDLE', 'THINKING', 'WORKING', 'WAITING', 'SUCCESS', 'ERROR', 'DISCONNECTED']);
function within(root, target) { const relative = path.relative(root, target); return relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative); }
function safeFile(root, relative) {
  if (typeof relative !== 'string' || relative.includes('\0') || relative.includes('\\')) return null;
  const file = path.resolve(root, relative);
  try { return within(root, file) && within(fs.realpathSync(root), fs.realpathSync(file)) && fs.statSync(file).isFile() ? file : null; } catch { return null; }
}
class PresentationHost {
  constructor(config) {
    this.config = config;
    this.counter = 1;
    this.seq = -1;
    this.readies = 0;
    this.current = null;
    this.receipts = new Map();
    this.state = { sections: {}, pets: { yunlan: {} } };
    this.sleepFile = typeof config.sleepFile === 'string' ? config.sleepFile : null;
    this.sleep = this.loadSleep();
    if (this.sleep) this.leaf('sleep', this.sleep);
  }
  animationNames() {
    const a = this.config.renderConfig?.main?.animations || {};
    const names = new Set();
    for (const key of ['idle', 'turn', 'drag', 'clicks']) for (const name of a[key] || []) names.add(name);
    for (const action of a.moves?.actions || []) names.add(action.name);
    for (const category of a.categories || []) for (const name of category.actions || []) names.add(name);
    for (const slots of Object.values(a.events || {})) for (const slot of slots || []) {
      if (typeof slot === 'string') names.add(slot); else for (const name of slot || []) names.add(name);
    }
    for (const set of a.sleep || []) for (const key of ['start', 'loop', 'end']) for (const name of set[key] || []) names.add(name);
    return names;
  }
  loadSleep() {
    if (!this.sleepFile) return null;
    try {
      const data = JSON.parse(fs.readFileSync(this.sleepFile, 'utf8'));
      const names = this.animationNames();
      if (data?.sleeping === true && ['start', 'loop', 'end'].every(key => typeof data[key] === 'string' && names.has(data[key]))) {
        return { sleeping: true, start: data.start, loop: data.loop, end: data.end };
      }
    } catch {}
    return null;
  }
  persistSleep() {
    if (!this.sleepFile) return;
    try {
      if (this.sleep) fs.writeFileSync(this.sleepFile, JSON.stringify(this.sleep));
      else if (fs.existsSync(this.sleepFile)) fs.unlinkSync(this.sleepFile);
    } catch {}
  }
  setSleep(sleeping) {
    if (sleeping) {
      const sets = this.config.renderConfig?.main?.animations?.sleep;
      if (!Array.isArray(sets) || sets.length === 0) return false;
      const set = sets[Math.floor(Math.random() * sets.length)];
      const pick = (pool) => Array.isArray(pool) && pool.length ? pool[Math.floor(Math.random() * pool.length)] : null;
      const start = pick(set.start), loop = pick(set.loop), end = pick(set.end);
      if (!start || !loop || !end) return false;
      this.sleep = { sleeping: true, start, loop, end };
    } else {
      this.sleep = { sleeping: false };
    }
    this.leaf('sleep', this.sleep);
    this.persistSleep();
    return true;
  }
  leaf(key, value) { this.state.pets.yunlan[key] = { counter: ++this.counter, data: value }; }
  apply(message) {
    if (!message || typeof message !== 'object') return false;
    if (this.sleep?.sleeping === true) return false;
    if (message.kind === 'clear') { this.leaf('say', { kind: 'clear' }); this.current = null; return true; }
    if (message.kind !== 'presentation' || !Number.isSafeInteger(message.seq) || message.seq <= this.seq ||
      typeof message.text !== 'string' || message.text.length > 2000 || !STATES.has(message.state) ||
      !Number.isFinite(message.ttlMs) || message.ttlMs < 0 || message.ttlMs > 600000) return false;
    this.seq = message.seq;
    this.leaf('say', message.text ? { text: message.text, duration: message.ttlMs, anim: false } : { kind: 'clear' });
    let animation = this.animationNames().has(message.intent) ? message.intent
      : (this.config.intentMap[message.intent] || this.config.stateMap[message.state]);
    if (animation) {
      const id = 'yunlan-' + message.seq;
      this.current = id;
      this.receipts.set(id, { playbackId: id, petId: 'yunlan', name: animation, status: 'accepted' });
      while (this.receipts.size > 100) this.receipts.delete(this.receipts.keys().next().value);
      this.leaf('anim', { name: animation, playbackId: id });
    }
    return true;
  }
  route(url, method, body) {
    if (url.pathname === '/dsh-pet-7340/config' && method === 'GET') return { status: 200, value: this.config.renderConfig };
    if (url.pathname === '/dsh-pet-7340/state' && method === 'GET') return { status: 200, value: this.state };
    if ((url.pathname === '/dsh-pet-7340/sleep' || url.pathname === '/dsh-pet-7340/wake') && method === 'POST') {
      if (url.searchParams.get('pet') !== 'yunlan') return { status: 400, value: { ok: false } };
      const sleeping = url.pathname.endsWith('/sleep');
      return this.setSleep(sleeping) ? { status: 200, value: { ok: true } } : { status: 503, value: { ok: false } };
    }
    const report = /^\/dsh-pet-7340\/playback\/(yunlan-\d+)\/report$/.exec(url.pathname);
    if (report && method === 'POST') {
      const entry = this.receipts.get(report[1]);
      if (!entry || !['playing', 'ended', 'failed', 'cancelled'].includes(body?.status)) return { status: 400, value: { ok: false } };
      if (!['ended', 'failed', 'cancelled'].includes(entry.status)) entry.status = body.status;
      return { status: 200, value: { ok: true } };
    }
    if (url.pathname === '/dsh-pet-7340/pose/report' && method === 'POST') return { status: 200, value: { ok: true } };
    if (url.pathname === '/dsh-pet-7340/menu/events' && method === 'GET') return { status: 200, value: { events: [] } };
    return { status: 403, value: { ok: false, reason: 'not-enabled' } };
  }
}
module.exports = { PIN, COMMANDS, STATES, safeFile, within, PresentationHost };
