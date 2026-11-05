/**
 * Yunlan menu UI: compact grouped panel with lazily built action submenus.
 *
 * The root panel only contains the common commands. Category and action panels
 * are created on demand, so opening the menu never builds 100+ DOM nodes.
 * No network, no host data, no innerHTML for labels.
 */
(function () {
  'use strict';
  const ICONS = {
    chat: '<svg viewBox="0 0 24 24"><path d="M4 5.5h16v10H9l-5 4v-14z"/></svg>',
    growth: '<svg viewBox="0 0 24 24"><path d="M12 20V9"/><path d="M12 13c-4 0-7-2.5-7-7 4 0 7 2.5 7 7z"/><path d="M12 10c0-3.5 2.5-6 6-6 0 3.5-2.5 6-6 6z"/></svg>',
    like: '<svg viewBox="0 0 24 24"><path d="M12 20s-7-4.4-7-9.5A4.5 4.5 0 0 1 12 7a4.5 4.5 0 0 1 7 3.5C19 15.6 12 20 12 20z"/></svg>',
    dislike: '<svg viewBox="0 0 24 24"><path d="M12 20s-7-4.4-7-9.5A4.5 4.5 0 0 1 12 7a4.5 4.5 0 0 1 7 3.5C19 15.6 12 20 12 20z"/><path d="M2 2l20 20"/></svg>',
    sleep: '<svg viewBox="0 0 24 24"><path d="M20 15.5A8 8 0 0 1 8.5 4 8 8 0 1 0 20 15.5z"/></svg>',
    wake: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/></svg>',
    roam: '<svg viewBox="0 0 24 24"><circle cx="13" cy="4" r="2"/><path d="M11 8l-2 4 3 2 1 6M9 12l-4 1M14 14l4 2"/></svg>',
    motion: '<svg viewBox="0 0 24 24"><path d="M3 8h12M3 12h16M3 16h9"/></svg>',
    hide: '<svg viewBox="0 0 24 24"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z"/><circle cx="12" cy="12" r="2.5"/><path d="M3 3l18 18"/></svg>',
    actions: '<svg viewBox="0 0 24 24"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 16l.9 2.1L22 19l-2.1.9L19 22l-.9-2.1L16 19l2.1-.9z"/></svg>',
    settings: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2 3.4-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5v.2h-4v-.2a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1-2-3.4.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H4v-4h.2a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1 2-3.4.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.5V4h4v.2a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1 2 3.4-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.5 1h.2v4h-.2a1.7 1.7 0 0 0-1.5 1z"/></svg>',
    mic: '<svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6"/></svg>',
    cloud: '<svg viewBox="0 0 24 24"><path d="M7 18h10a4 4 0 0 0 .5-8 6 6 0 0 0-11.5 1.5A3.5 3.5 0 0 0 7 18z"/></svg>',
    plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
    minus: '<svg viewBox="0 0 24 24"><path d="M5 12h14"/></svg>',
    talk: '<svg viewBox="0 0 24 24"><path d="M4 5h16v10H9l-5 4V5z"/><path d="M8 9h8M8 12h5"/></svg>',
    spin: '<svg viewBox="0 0 24 24"><path d="M20 12a8 8 0 1 1-3-6.2"/><path d="M20 4v5h-5"/></svg>',
  };
  const CSS = `
  .yl-menu-root{position:fixed;inset:0;z-index:2147483000;pointer-events:none}
  .yl-menu-panel{position:absolute;pointer-events:auto;min-width:300px;max-width:340px;max-height:calc(100vh - 16px);
    overflow:auto;background:rgba(251,253,253,.985);border:1px solid rgba(35,78,88,.16);border-radius:13px;
    box-shadow:0 14px 36px rgba(10,35,45,.22),0 2px 8px rgba(10,35,45,.10);padding:6px;
    font:13px/1.3 "Microsoft YaHei UI","Segoe UI",sans-serif;color:#22363d;user-select:none;
    scrollbar-width:thin;scrollbar-color:rgba(70,110,120,.28) transparent}
  .yl-menu-panel::-webkit-scrollbar{width:8px}
  .yl-menu-panel::-webkit-scrollbar-thumb{background:rgba(70,110,120,.25);border-radius:8px}
  .yl-menu-section{padding:6px 9px 2px;font-size:10.5px;font-weight:700;color:#7b9298;letter-spacing:.10em}
  .yl-menu-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1px;padding:0 2px}
  .yl-menu-grid.cols-2{grid-template-columns:repeat(2,minmax(0,1fr))}
  .yl-menu-grid .yl-menu-item{min-width:0;padding:4px 6px;gap:5px}
  .yl-menu-item{display:flex;align-items:center;gap:9px;min-height:29px;padding:4px 9px;border-radius:8px;
    cursor:default;white-space:nowrap;outline:none}
  .yl-menu-item:hover,.yl-menu-item:focus{background:#e6f2f3}
  .yl-menu-icon{width:18px;height:18px;flex:0 0 18px;display:flex;align-items:center;justify-content:center;color:#527e85}
  .yl-menu-icon svg{width:17px;height:17px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
  .yl-menu-label{flex:1}
  .yl-menu-check{color:#16898b;font-weight:700;font-size:13px}
  .yl-menu-arrow{color:#8aa1a7;font-size:15px;line-height:1}
  .yl-menu-sep{height:1px;background:rgba(40,80,90,.10);margin:5px 10px}
  `;
  let installed = false;
  function install() {
    if (installed) return;
    installed = true;
    const style = document.createElement('style');
    style.id = 'yl-menu-style';
    style.textContent = CSS;
    document.head.appendChild(style);
  }
  function node(tag, cls, text) {
    const value = document.createElement(tag);
    if (cls) value.className = cls;
    if (text !== undefined) value.textContent = text;
    return value;
  }
  function icon(name) {
    const holder = node('span', 'yl-menu-icon');
    if (ICONS[name]) holder.innerHTML = ICONS[name];
    return holder;
  }
  function mount(options) {
    install();
    const { sections, x, y, clamp, onAction, onClose } = options;
    const root = node('div', 'yl-menu-root');
    const panels = new Set();
    let closed = false;
    let pointerMoved = false;
    const trackPointer = (event) => {
      if (Math.hypot(event.clientX - x, event.clientY - y) > 6) pointerMoved = true;
    };
    const close = () => {
      if (closed) return;
      closed = true;
      document.removeEventListener('mousedown', outside, true);
      document.removeEventListener('keydown', escape, true);
      document.removeEventListener('mousemove', trackPointer, true);
      root.remove();
      if (onClose) onClose();
    };
    const outside = (event) => { if (!root.contains(event.target)) close(); };
    const escape = (event) => { if (event.key === 'Escape') { event.preventDefault(); close(); } };

    function place(panel, left, top) {
      const view = clamp && [clamp.x, clamp.y, clamp.w, clamp.h].every(Number.isFinite)
        ? clamp : { x: 0, y: 0, w: window.innerWidth, h: window.innerHeight };
      // The visible clamp shrinks when the pet is near a screen edge. Cap the
      // panel to that area so its own scrollbar can always reach the last row.
      panel.style.maxHeight = Math.max(80, view.h - 12) + 'px';
      const width = panel.offsetWidth, height = panel.offsetHeight;
      panel.style.left = Math.max(view.x + 6, Math.min(left, view.x + view.w - width - 6)) + 'px';
      panel.style.top = Math.max(view.y + 6, Math.min(top, view.y + view.h - height - 6)) + 'px';
    }
    function submenu(parent, anchor, children, title) {
      const panel = node('div', 'yl-menu-panel');
      if (title) panel.appendChild(node('div', 'yl-menu-section', title));
      for (const child of children) {
        const item = node('div', 'yl-menu-item');
        item.tabIndex = 0;
        if (child.icon) item.appendChild(icon(child.icon));
        item.appendChild(node('span', 'yl-menu-label', child.label));
        if (child.checked) item.appendChild(node('span', 'yl-menu-check', '✓'));
        if (Array.isArray(child.children) && child.children.length) {
          item.appendChild(node('span', 'yl-menu-arrow', '›'));
          item.addEventListener('mouseenter', () => { if (pointerMoved) openChild(item, panel, child.children, child.title); });
          item.addEventListener('mousemove', () => { if (pointerMoved) openChild(item, panel, child.children, child.title); });
          item.addEventListener('focus', () => openChild(item, panel, child.children, child.title));
        } else {
          item.addEventListener('click', (event) => {
            event.preventDefault(); event.stopPropagation();
            close();
            if (onAction) onAction(child);
          });
        }
        panel.appendChild(item);
      }
      root.appendChild(panel); panels.add(panel);
      panel.style.visibility = 'hidden'; panel.style.display = 'block';
      const box = parent.getBoundingClientRect(), itemBox = anchor.getBoundingClientRect();
      const view = clamp && [clamp.x, clamp.y, clamp.w, clamp.h].every(Number.isFinite)
        ? clamp : { x: 0, y: 0, w: window.innerWidth, h: window.innerHeight };
      let left = box.right + 4;
      if (left + panel.offsetWidth > view.x + view.w - 6) left = box.left - panel.offsetWidth - 4;
      place(panel, left, itemBox.top);
      panel.style.visibility = '';
      return panel;
    }
    const childByItem = new Map();
    function removeChild(item) {
      const panel = childByItem.get(item);
      if (!panel) return;
      for (const [key] of [...childByItem]) {
        if (key.parentElement === panel) removeChild(key);
      }
      panel.remove();
      childByItem.delete(item);
      panels.delete(panel);
    }
    function openChild(item, panel, children, title) {
      const previous = childByItem.get(item);
      if (previous) return;
      for (const [key] of [...childByItem]) {
        if (key.parentElement === item.parentElement && key !== item) removeChild(key);
      }
      const child = submenu(panel, item, children, title);
      childByItem.set(item, child);
    }
    function rootPanel() {
      const panel = node('div', 'yl-menu-panel');
      for (const section of sections) {
        if (!section.items || !section.items.length) continue;
        if (panel.childElementCount) panel.appendChild(node('div', 'yl-menu-sep'));
        panel.appendChild(node('div', 'yl-menu-section', section.label));
        const container = section.grid ? node('div', 'yl-menu-grid' + (section.grid === 2 ? ' cols-2' : '')) : panel;
        if (section.grid) panel.appendChild(container);
        for (const entry of section.items) {
          const item = node('div', 'yl-menu-item');
          item.tabIndex = 0;
          if (entry.icon) item.appendChild(icon(entry.icon));
          item.appendChild(node('span', 'yl-menu-label', entry.label));
          if (entry.checked) item.appendChild(node('span', 'yl-menu-check', '✓'));
          if (Array.isArray(entry.children) && entry.children.length) {
            item.appendChild(node('span', 'yl-menu-arrow', '›'));
            item.addEventListener('mouseenter', () => { if (pointerMoved) openChild(item, panel, entry.children, entry.title); });
            item.addEventListener('mousemove', () => { if (pointerMoved) openChild(item, panel, entry.children, entry.title); });
            item.addEventListener('focus', () => openChild(item, panel, entry.children, entry.title));
            item.addEventListener('click', (event) => {
              event.preventDefault(); event.stopPropagation();
              openChild(item, panel, entry.children, entry.title);
            });
          } else {
            item.addEventListener('click', (event) => {
              event.preventDefault(); event.stopPropagation();
              close();
              if (onAction) onAction(entry);
            });
          }
          container.appendChild(item);
        }
      }
      root.appendChild(panel); panels.add(panel);
      panel.style.visibility = 'hidden'; panel.style.display = 'block';
      place(panel, x, y);
      panel.style.visibility = '';
    }
    document.body.appendChild(root);
    root.addEventListener('contextmenu', (event) => event.preventDefault());
    root.addEventListener('mousedown', (event) => event.stopPropagation());
    document.addEventListener('mousedown', outside, true);
    document.addEventListener('keydown', escape, true);
    document.addEventListener('mousemove', trackPointer, true);
    rootPanel();
    return { close, element: root };
  }
  window.YunlanMenu = { mount };
  install();
})();
