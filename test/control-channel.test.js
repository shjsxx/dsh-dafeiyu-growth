'use strict';
import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { createConnectionHandler, validate } from '../desktop/control-channel.cjs';
const key = 'a'.repeat(64);
class Socket extends EventEmitter {
  destroyed = false;
  destroy() { if (!this.destroyed) { this.destroyed = true; this.emit('close'); } }
  data(value) { this.emit('data', Buffer.isBuffer(value) ? value : Buffer.from(value)); }
}
function fixture(t, extra = {}) {
  const messages = []; let disconnects = 0;
  const handler = createConnectionHandler({ key, onMessage: m => messages.push(m), onDisconnect: () => disconnects++, ...extra });
  t.after(() => handler.shutdown());
  const connect = () => { const s = new Socket(); handler(s); return s; };
  return { handler, messages, connect, disconnects: () => disconnects };
}
const auth = JSON.stringify({ kind: 'handshake', key }) + '\n';
test('strict named pipe and key validation', () => {
  const prefix = String.raw`\\.\pipe\yunlan-renderer-`;
  assert.doesNotThrow(() => validate(prefix + 'b'.repeat(32), key));
  for (const pipe of ['127.0.0.1:1234', prefix, prefix + 'b'.repeat(33), prefix + '../x', prefix + 'B'.repeat(32)]) assert.throws(() => validate(pipe, key));
  assert.throws(() => validate(prefix + 'b'.repeat(32), 'secret'));
});
test('fragmented authentication and UTF8 JSONL coalesced messages', t => {
  const f = fixture(t), s = f.connect();
  s.data(auth.slice(0, 13)); s.data(auth.slice(13) + '{"kind":"clear"}\n');
  const text = Buffer.from('{"kind":"presentation","text":"云岚"}\n');
  for (const byte of text) s.data(Buffer.from([byte]));
  assert.deepEqual(f.messages, [{ kind: 'clear' }, { kind: 'presentation', text: '云岚' }]);
  s.emit('end'); s.emit('error', new Error('private diagnostic'));
  assert.equal(f.disconnects(), 1);
});
test('unauthenticated first data cannot reserve ownership', t => {
  const f = fixture(t), attacker = f.connect(), owner = f.connect();
  attacker.data('{'); owner.data(auth);
  assert.equal(attacker.destroyed, true);
  owner.data('{"kind":"shutdown"}\n');
  assert.deepEqual(f.messages, [{ kind: 'shutdown' }]);
  assert.equal(f.disconnects(), 0);
  assert.equal(f.connect().destroyed, true);
  owner.destroy();
  assert.equal(f.disconnects(), 1);
  assert.equal(f.connect().destroyed, true);
});
test('wrong handshake malformed UTF8 objects and oversized input rejected', t => {
  for (const bad of ['{"kind":"shutdown"}\n', JSON.stringify({ kind: 'handshake', key: 'b'.repeat(64) }) + '\n', '[]\n', 'null\n', '{bad}\n', 'x'.repeat(8193), Buffer.from([255, 10])]) {
    const f = fixture(t), s = f.connect(); s.data(bad);
    assert.equal(s.destroyed, true); assert.equal(f.disconnects(), 0); assert.deepEqual(f.messages, []);
  }
});
test('authenticated line limit and callback errors disconnect once', t => {
  const f = fixture(t), s = f.connect(); s.data(auth); s.data('x'.repeat(16385));
  assert.equal(s.destroyed, true); assert.equal(f.disconnects(), 1);
  const g = fixture(t, { onMessage() { throw new Error('private'); } }), q = g.connect();
  q.data(auth + '{"kind":"clear"}\n'); assert.equal(q.destroyed, true); assert.equal(g.disconnects(), 1);
});
test('handshake timeout rejects without disconnect callback', async t => {
  const f = fixture(t, { handshakeTimeoutMs: 10 }), s = f.connect();
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(s.destroyed, true); assert.equal(f.disconnects(), 0);
});
test('shutdown cleans sockets without owner disconnect callback', t => {
  const f = fixture(t), s = f.connect(); s.data(auth); f.handler.shutdown();
  assert.equal(s.destroyed, true); assert.equal(f.disconnects(), 0);
  assert.equal(f.connect().destroyed, true);
});
