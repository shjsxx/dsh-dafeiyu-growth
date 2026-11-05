'use strict';
const net = require('node:net');
const crypto = require('node:crypto');
const { TextDecoder } = require('node:util');
const PREFIX = String.raw`\\.\pipe\yunlan-renderer-`;
function validate(pipe, key) {
  if (typeof pipe !== 'string' || !pipe.startsWith(PREFIX) || !/^[a-f0-9]{32}$/.test(pipe.slice(PREFIX.length))) throw new TypeError('Invalid control pipe');
  if (typeof key !== 'string' || !/^[a-f0-9]{64}$/.test(key)) throw new TypeError('Invalid control credential');
}
// A connection becomes owner only after a complete valid authentication record.
// An unauthenticated peer cannot reserve the channel or trigger disconnection.
function createConnectionHandler({ key, onMessage, onDisconnect, handshakeTimeoutMs = 2000 }) {
  if (typeof key !== 'string' || !/^[a-f0-9]{64}$/.test(key)) throw new TypeError('Invalid control credential');
  const expected = Buffer.from(key, 'ascii');
  const sockets = new Set();
  let owner = null, claimed = false, closing = false;
  function accept(socket) {
    if (claimed || closing) { socket.on('error', () => {}); socket.destroy(); return; }
    sockets.add(socket);
    let authenticated = false, ended = false, buffered = Buffer.alloc(0);
    const decoder = new TextDecoder('utf-8', { fatal: true });
    const timer = setTimeout(() => socket.destroy(), handshakeTimeoutMs);
    timer.unref?.();
    function finish() {
      if (ended) return;
      ended = true;
      clearTimeout(timer);
      sockets.delete(socket);
      if (authenticated && !closing) onDisconnect?.();
    }
    socket.on('error', () => { socket.destroy(); finish(); });
    socket.on('end', () => { socket.destroy(); finish(); });
    socket.on('close', finish);
    socket.on('data', chunk => {
      if (ended || closing || (claimed && owner !== socket)) return socket.destroy();
      const data = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      // Process each fragment before appending, so huge chunks cannot allocate an unbounded line.
      let offset = 0;
      while (offset < data.length && !ended && !socket.destroyed) {
        const newline = data.indexOf(10, offset);
        const end = newline < 0 ? data.length : newline;
        const limit = authenticated ? 16384 : 8192;
        if (buffered.length + end - offset > limit) { socket.destroy(); return; }
        buffered = Buffer.concat([buffered, data.subarray(offset, end)]);
        offset = end + (newline < 0 ? 0 : 1);
        if (newline < 0) break;
        let message;
        try {
          message = JSON.parse(decoder.decode(buffered));
        } catch { socket.destroy(); return; }
        buffered = Buffer.alloc(0);
        if (!message || typeof message !== 'object' || Array.isArray(message)) { socket.destroy(); return; }
        if (!authenticated) {
          const candidate = typeof message.key === 'string' && /^[a-f0-9]{64}$/.test(message.key) ? Buffer.from(message.key, 'ascii') : null;
          if (message.kind !== 'handshake' || !candidate || !crypto.timingSafeEqual(candidate, expected) || claimed) { socket.destroy(); return; }
          authenticated = true;
          claimed = true;
          owner = socket;
          clearTimeout(timer);
          for (const other of sockets) if (other !== socket) other.destroy();
        } else {
          try { onMessage?.(message); } catch { socket.destroy(); return; }
        }
      }
    });
  }
  accept.shutdown = () => {
    closing = true;
    for (const socket of sockets) socket.destroy();
    sockets.clear();
  };
  return accept;
}
function createControlServer({ pipe, key, onMessage, onDisconnect, handshakeTimeoutMs }) {
  validate(pipe, key);
  const handler = createConnectionHandler({ key, onMessage, onDisconnect, handshakeTimeoutMs });
  const server = net.createServer(handler);
  server.shutdown = () => { handler.shutdown(); server.close(); };
  server.listen(pipe);
  return server;
}
module.exports = { createControlServer, createConnectionHandler, validate };
