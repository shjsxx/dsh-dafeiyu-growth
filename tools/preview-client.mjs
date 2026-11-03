// Offline preview ONLY: synthetic fixture, no HTTP server, no owner-profile reads.
import {build} from 'esbuild'
import {mkdir, writeFile} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
import path from 'node:path'
const root = fileURLToPath(new URL('../',import.meta.url))
const out = path.join(root,'native/verification/web-dashboard-preview.html')
const result = await build({absWorkingDir:root, bundle:true,write:false,format:'iife',platform:'browser',target:['chrome110'],loader:{'.css':'text'},charset:'utf8',minify:true,
  define:{'process.env.NODE_ENV':'"production"'},
  stdin:{resolveDir:root,loader:'jsx',contents:`
    import React from 'react'; import {createRoot} from 'react-dom/client';
    import {SettingsDashboard} from './web/App.jsx'; import {growthFixture} from './test/fixtures/growth-view.js';
    let state = growthFixture();
    globalThis.fetch = async (url, options) => {
      if (url !== '/plugins/dsh-dafeiyu-growth/state' && url !== '/plugins/dsh-dafeiyu-growth/action') throw Error('Offline preview');
      if (options.method === 'POST') { state = {...growthFixture(),xp:0,level:1,stage:{key:'egg'},ratio:0,into:0,streak:0,counters:{},traits:{},learned:[],diary:[]}; return {ok:true,json:async () => ({ok:true,state})}; }
      return {ok:true,json:async () => state};
    };
    createRoot(document.getElementById('app')).render(<SettingsDashboard/>);
    document.getElementById('theme').onclick = () => document.documentElement.classList.toggle('dark');
  `}})
await mkdir(path.dirname(out),{recursive:true})
const html = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>云岚成长手记 · 离线合成预览</title><style>body{margin:0;padding:24px;background:#eef1ee;font-family:system-ui,sans-serif}aside{max-width:960px;margin:0 auto 16px;color:#53675d;font-size:12px;display:flex;gap:12px;justify-content:space-between;align-items:center}aside button{border:1px solid #ccd5ce;background:#fff;padding:7px 10px;border-radius:7px;cursor:pointer;white-space:nowrap}#app{max-width:960px;margin:auto}.dark body{background:#111b16}.dark aside{color:#b4c3b9}@media(max-width:500px){body{padding:12px}}</style><aside><span>离线合成预览 · 所有数字均为测试夹具<br>不读取真实档案，不连接 DSH 或云端。此处清零仅重置演示数据。</span><button id="theme">切换深浅色</button></aside><div id="app"></div><script>${result.outputFiles[0].text.replace(/<\/script/gi,'<\\/script')}</script></html>`
await writeFile(out,html,'utf8')
console.log('Synthetic offline preview: native/verification/web-dashboard-preview.html')
