// Yunlan's restricted host for the real pinned dsh-pet renderer. No HTTP listener or model services.
const fs = require('node:fs');
const path = require('node:path');
const PIN = '3c99b1bb4d1bcb831880e8871718c50b64af0d9b';
const COMMANDS = new Set(['open-menu', 'open-conversation', 'open-growth', 'open-voice-tools', 'open-cloud-settings', 'open-realtime-settings', 'emergency-stop', 'show', 'hide', 'feedback-positive', 'feedback-negative', 'interact-head', 'interact-poke']);
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
  }
  leaf(key, value) { this.state.pets.yunlan[key] = { counter: ++this.counter, data: value }; }
  apply(message) {
    if (!message || typeof message !== 'object') return false;
    if (message.kind === 'clear') { this.leaf('say', { kind: 'clear' }); this.current = null; return true; }
    if (message.kind !== 'presentation' || !Number.isSafeInteger(message.seq) || message.seq <= this.seq ||
      typeof message.text !== 'string' || message.text.length > 2000 || !STATES.has(message.state) ||
      !Number.isFinite(message.ttlMs) || message.ttlMs < 0 || message.ttlMs > 600000) return false;
    this.seq = message.seq;
    this.leaf('say', message.text ? { text: message.text, duration: message.ttlMs, anim: false } : { kind: 'clear' });
    let animation = this.config.intentMap[message.intent] || this.config.stateMap[message.state];
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
