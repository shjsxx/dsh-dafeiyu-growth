// Preload intentionally exposes no file access, HTTP, screen capture, microphone, shell or model APIs.
const { contextBridge, ipcRenderer } = require('electron');
const commands = new Set(['open-menu', 'open-conversation', 'open-growth', 'open-evolution', 'open-research', 'open-memory-curation', 'open-persona', 'stop-research-scheduler', 'stop-memory-health', 'stop-persona-health', 'open-voice-tools', 'open-cloud-settings', 'open-realtime-settings', 'emergency-stop', 'show', 'hide', 'feedback-positive', 'feedback-negative', 'interact-head', 'interact-poke', 'toggle-roam', 'toggle-reduced', 'toggle-chatter', 'spin', 'scale-up', 'scale-down']);
contextBridge.exposeInMainWorld('yunlanBridge', {
  command(name) { if (commands.has(name)) ipcRenderer.send('yunlan:command', name); },
  action(payload) {
    if (!payload || typeof payload.name !== 'string' || payload.name.length > 80) return;
    if (!['move', 'jump', 'hop', 'action', 'click'].includes(payload.kind)) return;
    const distance = Number.isFinite(payload.distance) ? Math.max(0, Math.min(10000, payload.distance)) : 0;
    ipcRenderer.send('yunlan:action', { name: payload.name, kind: payload.kind, distance });
  },
  ready() { ipcRenderer.send('yunlan:ready'); },
  setMenuOpen(open) { ipcRenderer.send('yunlan:menu-open', open === true); },
  onControl(callback) { ipcRenderer.on('yunlan:control', (_, value) => callback(value)); },
});
contextBridge.exposeInMainWorld('petBridge', {
  setBounds(x,y,width,height,boxX,boxY,size,bottomPad,vx,vy,marginL,marginT) { ipcRenderer.send('pet:set-bounds', {x,y,width,height,boxX,boxY,size,bottomPad,vx,vy,marginL,marginT}); },
  setInteractive(value) { ipcRenderer.send('pet:set-interactive', value === true); },
  setInputBusy(value) { ipcRenderer.send('pet:input-busy', value === true); },
  setInputRegions() {},
  requestKeyboardFocus() { ipcRenderer.send('yunlan:focus'); },
  openDshSite() { ipcRenderer.send('yunlan:command', 'open-conversation'); },
  focusDshHost() {},
  reportFlight() {}, onFlightStates() {}, reportCollide() {}, onPetHit() {},
  onDisplays(callback) { ipcRenderer.on('pet:displays', (_, value) => callback(value)); },
});
