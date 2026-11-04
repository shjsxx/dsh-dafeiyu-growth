// Preload intentionally exposes no file access, HTTP, screen capture, microphone, shell or model APIs.
const { contextBridge, ipcRenderer } = require('electron');
const commands = new Set(['open-menu', 'open-conversation', 'open-growth', 'open-voice-tools', 'open-cloud-settings', 'open-realtime-settings', 'emergency-stop', 'show', 'hide', 'feedback-positive', 'feedback-negative', 'interact-head', 'interact-poke']);
contextBridge.exposeInMainWorld('yunlanBridge', {
  command(name) { if (commands.has(name)) ipcRenderer.send('yunlan:command', name); },
  ready() { ipcRenderer.send('yunlan:ready'); },
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
