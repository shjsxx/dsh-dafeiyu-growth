// Loaded after pinned dsh-pet classes but before boot; retain animation/physics, replace unrelated services.
'use strict';
let stoppedByCore = false;
let reducedByCore = false;
document.addEventListener('play', event => {if ((stoppedByCore || reducedByCore) && event.target instanceof HTMLMediaElement) event.target.pause();}, true);
const originalSwitchTo = PetSprite.prototype.switchTo;
PetSprite.prototype.switchTo = function(...args) {
  if (!stoppedByCore) return originalSwitchTo.apply(this, args);
};
// Right click keeps the full pinned action tree locally (every configured
// animation), plus one explicit entry to the native core menu. No host cloud,
// balance, todo or chat endpoint is exposed to the renderer.
PetSprite.prototype.onContextMenu = function(event) {
  if (this.dragState.active || this.dragState.dragging || this.justDragged || this.menuOpen) return;
  event.preventDefault();
  this.stopThrow(); this.stopMove();
  const coreItem = { label: '云岚菜单…（对话 / 成长 / 设置）', action: 'core-menu' };
  const tree = [coreItem].concat(window.PetShared.buildMenuTree(this.animations));
  this.menuOpen = true;
  window.__dshPetDebug.menuOpen = true;
  this.setInteractive(true);
  this.syncInputBusy();
  const menu = window.PetShared.mountContextMenu({
    tree,
    x: event.clientX,
    y: event.clientY,
    clamp: this.visibleClampRect(),
    onAction: (leaf) => {
      if (leaf && leaf.action === 'core-menu') {
        this.closeMenu();
        window.yunlanBridge.command('open-menu');
        return;
      }
      this.onMenuAction(leaf, undefined, true);
    },
    onClose: () => {
      this.menuOpen = false;
      window.__dshPetDebug.menuOpen = false;
      this.syncInputBusy();
      if (!this.inputBusy()) this.setInteractive(false);
    },
  });
  this.menuClose = menu.close;
  this.syncInputRegions();
};
window.yunlanBridge.onControl(function(message) {
  if (message.kind === 'stop') {
    stoppedByCore = true;
    for (const sprite of sprites) {
      sprite.gen++; sprite.pending = null; sprite.pendingMove = null;
      sprite.stopThrow(); sprite.stopMove(); sprite.closeMenu();
      for (const video of [sprite.videoA, sprite.videoB]) { if(video) {video.pause();video.onended=null;} }
      sprite.whisperOn = false; sprite.whisperText = ''; sprite.renderBubble();
    }
  } else if (message.kind === 'resume') {
    stoppedByCore = false;
    for (const sprite of sprites) sprite.playIdle();
  } else if (message.kind === 'configure') {
    reducedByCore = message.reducedMotion === true;
    if (sprites.some(sprite => sprite.size !== Math.round(360*message.scale))) {
      void boot();
    }
    for (const sprite of sprites) {
      sprite.pet.fixedEnabled = !message.motionEnabled;
      sprite.root?.style.setProperty('--dsh-pet-font-bubble', String(21*message.bubbleScale)+'px');
      if (reducedByCore) {sprite.stopThrow();sprite.stopMove();for(const v of [sprite.videoA,sprite.videoB])v?.pause();}
      else if (!stoppedByCore) sprite.playIdle();
    }
  }
});
// No upstream shortcuts may open cloud chat, todo capture, or a second decision system.
// Semantic petting only: a short press that does not turn into a drag is
// forwarded as head/poke. Raw coordinates and arbitrary text never cross.
let petPress = null;
document.addEventListener('pointerdown', function(event) {
  const hit = event.target.closest && event.target.closest('.pet-hit');
  if (!hit || event.button !== 0) { petPress = null; return; }
  petPress = { x: event.clientX, y: event.clientY, at: Date.now(), hit: hit };
}, true);
document.addEventListener('pointerup', function(event) {
  const press = petPress; petPress = null;
  if (!press || !press.hit.isConnected) return;
  if (Date.now() - press.at > 700) return;
  if (Math.hypot(event.clientX - press.x, event.clientY - press.y) > 4) return;
  const rect = press.hit.getBoundingClientRect();
  const relative = rect.height > 0 ? (event.clientY - rect.top) / rect.height : 0;
  window.yunlanBridge.command(relative < 0.5 ? 'interact-head' : 'interact-poke');
}, true);
window.addEventListener('keydown', function(event) {
  if (event.altKey || event.ctrlKey || event.metaKey) {
    if (event.code === 'Escape') window.yunlanBridge.command('emergency-stop');
  }
}, true);
const readyCheck = setInterval(function() {
  if (window.__dshPetDebug?.configOk && sprites.length) {
    clearInterval(readyCheck); window.yunlanBridge.ready();
  }
}, 100);
