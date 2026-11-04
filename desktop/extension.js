// Loaded after pinned dsh-pet classes but before boot; retain animation/physics, replace unrelated services.
'use strict';
PetSprite.prototype.playIdle = function() {
  this.pendingMove = null;
  this.anim = this.animations.idle[0]; this.once = false;
  this.switchTo(this.anim, false);
};
let stoppedByCore = false;
let reducedByCore = false;
document.addEventListener('play', event => {if ((stoppedByCore || reducedByCore) && event.target instanceof HTMLMediaElement) event.target.pause();}, true);
const originalSwitchTo = PetSprite.prototype.switchTo;
PetSprite.prototype.switchTo = function(...args) {
  if (!stoppedByCore) return originalSwitchTo.apply(this, args);
};
PetSprite.prototype.onContextMenu = function(event) {
  event.preventDefault();
  this.stopThrow(); this.stopMove();
  window.yunlanBridge.command('open-menu');
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
