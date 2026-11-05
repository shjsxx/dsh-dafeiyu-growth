// Loaded after pinned dsh-pet classes but before boot; retain animation/physics, replace unrelated services.
'use strict';
let stoppedByCore = false;
let reducedByCore = false;
let motionEnabled = true;
let reducedMotion = false;
let chatterEnabled = true;
// Promote the video layer without a crossfade. The upstream 0.18s opacity
// transition can show both poses at partial alpha (a ghost/blank frame) while
// the next WebM is starting. A same-task class swap is visually cleaner.
const videoLayerStyle = document.createElement('style');
videoLayerStyle.textContent = 'video.pet-video{will-change:transform;transform:translateZ(0);backface-visibility:hidden;transition:none!important;visibility:hidden}video.pet-video.is-front{visibility:visible}';
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
const facingProperty = Object.getOwnPropertyDescriptor(PetSprite.prototype, 'facing');
if (facingProperty?.set) {
  Object.defineProperty(PetSprite.prototype, 'facing', {
    configurable: facingProperty.configurable,
    enumerable: facingProperty.enumerable,
    get: facingProperty.get,
    set(value) {
      // Never mirror-flip the body while it is held. A queued turn/action must
      // not make the drag animation jump from left-facing to right-facing.
      if (this.dragState?.active || this.dragState?.dragging) return;
      facingProperty.set.call(this, value);
    },
  });
}
PetSprite.prototype.switchTo = function(...args) {
  if (stoppedByCore) return;
  // While the user is holding the pet, a turn/click/event animation must not
  // steal the front layer: those clips can be mirrored differently and show as
  // a left/right flash. The drag animation itself is still allowed.
  if (this.dragState?.active && typeof args[0] === 'string') {
    const drags = this.animations?.drag || [];
    if (drags.length && !drags.includes(args[0])) return;
  }
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
function actionCategories(animations) {
  const groups = [];
  const add = (label, names) => {
    const unique = [...new Set((names || []).filter(name => typeof name === 'string' && name))];
    if (unique.length) groups.push({ label, children: unique.map(name => ({ label: name, anim: name })) });
  };
  add('待机', animations.idle);
  add('转向', animations.turn);
  add('拖拽', animations.drag);
  add('点击回应', animations.clicks);
  add('移动', (animations.moves?.actions || []).map(action => action.name));
  for (const category of animations.categories || []) add(category.id, category.actions);
  const eventLabels = { workStatus: '工作状态', balance: '余额', whisper: '碎碎念' };
  for (const [key, slots] of Object.entries(animations.events || {})) {
    const names = [];
    for (const slot of slots || []) {
      if (typeof slot === 'string') names.push(slot);
      else if (Array.isArray(slot)) names.push(...slot);
    }
    add(eventLabels[key] || key, names);
  }
  return groups;
}

function menuSections(sprite) {
  const categories = actionCategories(sprite.animations);
  const actionCount = categories.reduce((sum, group) => sum + group.children.length, 0);
  return [
    { label: '常用', items: [
      { label: '和云岚聊天', icon: 'chat', command: 'open-conversation' },
      { label: '查看成长卡', icon: 'growth', command: 'open-growth' },
      { label: '喜欢刚才的动作', icon: 'like', command: 'feedback-positive' },
      { label: '这个动作不太行', icon: 'dislike', command: 'feedback-negative' },
    ] },
    { label: '陪伴', items: [
      { label: sprite.sleeping ? '起来' : '睡觉', icon: sprite.sleeping ? 'wake' : 'sleep',
        sleepAction: sprite.sleeping ? 'wake' : 'sleep' },
      { label: '桌面漫游', icon: 'roam', checked: motionEnabled, command: 'toggle-roam' },
      { label: '减少动态', icon: 'motion', checked: reducedMotion, command: 'toggle-reduced' },
      { label: '主动搭话', icon: 'talk', checked: chatterEnabled, command: 'toggle-chatter' },
      { label: '隐藏到托盘', icon: 'hide', command: 'hide' },
    ] },
    { label: `动作 · ${actionCount}`, grid: true,
      items: categories.map(category => ({ label: category.label, children: category.children })) },
    { label: '设置', grid: 2, items: [
      { label: '语音与实时语音', icon: 'mic', command: 'open-voice-tools' },
      { label: '云端接口', icon: 'cloud', command: 'open-cloud-settings' },
      { label: '界面放大', icon: 'plus', command: 'scale-up' },
      { label: '界面缩小', icon: 'minus', command: 'scale-down' },
    ] },
  ];
}
let menuWarmed = false;
function warmMenu() {
  if (menuWarmed || !sprites.length || !window.YunlanMenu) return;
  menuWarmed = true;
  try {
    const menu = window.YunlanMenu.mount({
      sections: menuSections(sprites[0]), x: -10000, y: -10000,
      clamp: { x: -10000, y: -10000, w: 20, h: 20 },
      onAction() {}, onClose() {},
    });
    menu.close();
  } catch { /* warm-up is best effort */ }
}

// One grouped panel. Only the root items exist up front; action categories and
// their 100+ leaves are built on hover, keeping right-click opening cheap.
PetSprite.prototype.onContextMenu = function(event) {
  if (this.dragState.active || this.dragState.dragging || this.justDragged || this.menuOpen) return;
  event.preventDefault();
  // showInactive windows may swallow the first click to activate; menus must be clickable.
  window.petBridge?.requestKeyboardFocus?.();
  this.stopThrow(); this.stopMove();
  if (!window.YunlanMenu) {
    window.yunlanBridge.command('open-menu');
    return;
  }
  const sections = menuSections(this);
  this.menuOpen = true;
  window.__dshPetDebug.menuOpen = true;
  window.yunlanBridge.setMenuOpen(true);
  this.setInteractive(true);
  this.syncInputBusy();
  const menu = window.YunlanMenu.mount({
    sections,
    x: event.clientX,
    y: event.clientY,
    clamp: this.visibleClampRect(),
    onAction: (entry) => {
      if (entry.command) {
        window.yunlanBridge.command(entry.command);
        return;
      }
      if (entry.sleepAction) {
        this.onMenuAction({ label: entry.label, action: entry.sleepAction }, undefined, false);
        return;
      }
      if (entry.anim) {
        this.onMenuAction({ label: entry.anim, anim: entry.anim }, undefined, false);
      }
    },
    onClose: () => {
      this.menuOpen = false;
      window.__dshPetDebug.menuOpen = false;
      window.yunlanBridge.setMenuOpen(false);
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
    chatterEnabled = message.chatterEnabled !== false;
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
    setTimeout(warmMenu, 1200);
  }
}, 100);
