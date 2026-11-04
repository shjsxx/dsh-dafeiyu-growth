// Loaded after pinned dsh-pet classes but before boot; retain animation/physics, replace unrelated services.
'use strict';
let stoppedByCore = false;
let reducedByCore = false;
let motionEnabled = true;
let reducedMotion = false;
// Promote the two video layers explicitly; transparent always-on-top windows
// otherwise repaint more than necessary during opacity crossfades.
const videoLayerStyle = document.createElement('style');
videoLayerStyle.textContent = 'video.pet-video{will-change:opacity,transform;transform:translateZ(0);backface-visibility:hidden}';
document.head.appendChild(videoLayerStyle);
document.addEventListener('play', event => {if ((stoppedByCore || reducedByCore) && event.target instanceof HTMLMediaElement) event.target.pause();}, true);
const originalSwitchTo = PetSprite.prototype.switchTo;
const lastActionReport = new Map();
function reportAction(name) {
  if (!name || !window.yunlanBridge) return;
  const animations = sprites[0] ? sprites[0].animations : null;
  if (!animations) return;
  if ((animations.idle || []).includes(name) || (animations.turn || []).includes(name)) return;
  if (name.startsWith('工作状态-') || name.startsWith('余额-') || name.startsWith('碎碎念-') || name.startsWith('睡觉-')) return;
  if ((animations.moves?.actions || []).some(action => action.name === name)) return; // tryMove reports distance
  const now = Date.now();
  if (now - (lastActionReport.get(name) || 0) < 800) return;
  lastActionReport.set(name, now);
  if (lastActionReport.size > 200) lastActionReport.clear();
  const kind = (animations.clicks || []).includes(name) ? 'click' : (/跳|跃/.test(name) ? 'jump' : 'action');
  window.yunlanBridge.action({ name, kind, distance: 0 });
}
PetSprite.prototype.switchTo = function(...args) {
  if (stoppedByCore) return;
  const result = originalSwitchTo.apply(this, args);
  if (typeof args[0] === 'string') reportAction(args[0]);
  return result;
};
const originalTryMove = PetSprite.prototype.tryMove;
PetSprite.prototype.tryMove = function(...args) {
  const result = originalTryMove.apply(this, args);
  if (typeof result === 'string') {
    const move = this.pendingMove;
    const distance = move ? Math.abs((move.targetRatio - move.startRatio) * VIEW.w) : 0;
    window.yunlanBridge.action({ name: result, kind: 'move', distance: Math.round(distance) });
  }
  return result;
};
// One compact panel: common actions first, the full animation tree once, and
// the native settings menu only behind "更多设置". No cloud/todo endpoints.
PetSprite.prototype.onContextMenu = function(event) {
  if (this.dragState.active || this.dragState.dragging || this.justDragged || this.menuOpen) return;
  event.preventDefault();
  // showInactive windows may swallow the first click to activate; menus must be clickable.
  window.petBridge?.requestKeyboardFocus?.();
  this.stopThrow(); this.stopMove();
  const command = (label, name) => ({ label, action: 'cmd:' + name });
  const tree = [
    command('和云岚聊天…', 'open-conversation'),
    command('查看成长卡', 'open-growth'),
    command('喜欢刚才的动作', 'feedback-positive'),
    command('这个动作不太行', 'feedback-negative'),
  ];
  if (Array.isArray(this.animations.sleep) && this.animations.sleep.length) {
    tree.push({ label: this.sleeping ? '起来' : '睡觉', action: this.sleeping ? 'wake' : 'sleep' });
  }
  tree.push(
    command(`桌面漫游  ${motionEnabled ? '✓' : '○'}`, 'toggle-roam'),
    command(`减少动态  ${reducedMotion ? '✓' : '○'}`, 'toggle-reduced'),
    command('隐藏到托盘', 'hide'),
  );
  tree.push(...window.PetShared.buildMenuTree(this.animations));
  tree.push(command('更多设置…（语音 / 云端 / 大小）', 'open-menu'));
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
      if (leaf && typeof leaf.action === 'string' && leaf.action.startsWith('cmd:')) {
        this.closeMenu();
        window.yunlanBridge.command(leaf.action.slice(4));
        return;
      }
      this.onMenuAction(leaf, undefined, false);
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
    reducedMotion = message.reducedMotion === true;
    motionEnabled = message.motionEnabled !== false;
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
  if (sprites.some(sprite => sprite.sleeping)) return; // a sleeping pet wakes through its own state machine
  if (Date.now() - press.at > 700) return;
  if (Math.hypot(event.clientX - press.x, event.clientY - press.y) > 4) return;
  const rect = press.hit.getBoundingClientRect();
  const relative = rect.height > 0 ? (event.clientY - rect.top) / rect.height : 0;
  window.yunlanBridge.command(relative < 0.5 ? 'interact-head' : 'interact-poke');
}, true);
// Presentation-only roaming heartbeat: keeps the shell visibly alive even when
// the core has no eligible motion decision. It never writes practice records by
// itself; semantic action events are reported separately.
setInterval(function() {
  if (stoppedByCore || reducedByCore) return;
  for (const sprite of sprites) {
    if (sprite.sleeping || sprite.menuOpen || sprite.dragState.active || sprite.dragState.dragging ||
        sprite.throwRef !== null || sprite.moveRef !== null || sprite.pendingMove) continue;
    if (Math.random() < 0.55) sprite.tryMove();
  }
}, 28000);
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
