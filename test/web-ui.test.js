import test from 'node:test'
import assert from 'node:assert/strict'
import React, {act} from 'react'
import {JSDOM} from 'jsdom'
import {loadClient} from './helpers/load-client.js'

const fixture = () => ({version:1, level:12,xp:4900,into:60,need:920,ratio:60/920,
  stage:{key:'fish',name:'御风'},streak:4,
  counters:{events:80,turns:18,okTurns:15,errorTurns:2,toolCalls:64,toolErrors:4,recoveries:2,approvals:3},
  traits:{stamina:42,wit:66,affinity:58,calm:88},
  learned:[{id:'tool',text:'最常用的工具是 read（24 次）',at:'2026-10-01T08:00:00Z'}],
  diary:[{kind:'stage',text:'合成夹具：进入御风阶段',at:'2026-10-01T08:00:00Z',level:10},
    {text:'合成夹具：完成一次检查',at:'2026-10-02T08:00:00Z',level:12}],
  createdAt:'2026-09-01T00:00:00Z',updatedAt:'2026-10-02T08:00:00Z',
  storageHealth:{ok:true,readable:true,writable:true},
})

async function mount(t, {state=fixture(), responder, fastTimeout=false} = {}) {
  const dom = new JSDOM('<div id="root"></div>', {url:'http://localhost/'})
  const oldWindow = globalThis.window, oldDocument = globalThis.document
  globalThis.window = dom.window; globalThis.document = dom.window.document
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const requests = [], downloads = [], revoked = []
  const urls = {createObjectURL:blob => { downloads.push(blob); return `blob:fixture-${downloads.length}` }, revokeObjectURL:url => revoked.push(url)}
  dom.window.HTMLAnchorElement.prototype.click = function() {}
  const fetch = async (path, options) => {
    requests.push({path,options})
    if (responder) return responder(path,options)
    return {ok:true,json:async () => options.method === 'POST' ? {ok:true,state:{...fixture(),xp:0,level:1,into:0,ratio:0,diary:[],learned:[]}} : state}
  }
  const client = loadClient({document:dom.window.document,fetch,URL:urls,
    ...(fastTimeout ? {setTimeout:(callback, ms) => setTimeout(callback, ms === 10000 ? 10 : ms)} : {})})
  const {createRoot} = await import('react-dom/client')
  const root = createRoot(dom.window.document.getElementById('root'))
  await act(async () => { root.render(React.createElement(client.registrations[0].component)); await new Promise(resolve => setTimeout(resolve,0)) })
  t.after(async () => { await act(async () => root.unmount()); dom.window.close(); globalThis.window = oldWindow; globalThis.document = oldDocument; delete globalThis.IS_REACT_ACT_ENVIRONMENT })
  const query = selector => dom.window.document.querySelector(selector)
  const button = text => [...dom.window.document.querySelectorAll('button')].find(item => item.textContent.trim() === text)
  const click = async item => { assert.ok(item,'button exists'); await act(async () => item.click()) }
  return {dom,requests,downloads,revoked,query,button,click}
}

test('actual bundled UI renders real normalized stats, attributes, empty-safe content', async t => {
  const ui = await mount(t)
  assert.match(ui.query('.ylg').textContent,/一起，慢慢长大/)
  assert.equal(ui.query('.ylg-level').textContent,'12')
  assert.equal(ui.query('[aria-label="当前等级经验进度"]').getAttribute('aria-valuenow'),'7')
  assert.equal(ui.dom.window.document.querySelectorAll('.ylg-trait').length,4)
  assert.match(ui.query('.ylg-insights').textContent,/read/)
  assert.equal(ui.requests.length,1)
  assert.equal(ui.requests[0].path,'/plugins/dsh-dafeiyu-growth/state')
  assert.equal(ui.requests[0].options.credentials,'same-origin')
})

test('tab keyboard navigation and history filters have real selection/focus behavior', async t => {
  const ui = await mount(t)
  const first = ui.query('[role="tab"]')
  await act(async () => { first.focus(); first.dispatchEvent(new ui.dom.window.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true})) })
  assert.equal(ui.query('[aria-selected="true"]').textContent,'记忆')
  assert.equal(ui.dom.window.document.activeElement,ui.query('[aria-selected="true"]'))
  const journalTab = [...ui.dom.window.document.querySelectorAll('[role="tab"]')].find(element => element.textContent.startsWith('成长回顾'))
  await act(async () => journalTab.click())
  assert.equal(ui.dom.window.document.querySelectorAll('.ylg-timeline li').length,2)
  await ui.click(ui.button('阶段成长'))
  assert.equal(ui.dom.window.document.querySelectorAll('.ylg-timeline li').length,1)
  assert.match(ui.query('.ylg-timeline').textContent,/进入御风/)
  await ui.click(ui.button('日常回顾'))
  assert.match(ui.query('.ylg-timeline').textContent,/完成一次检查/)
})

test('untrusted diary text renders as text, not executable HTML', async t => {
  const state = fixture(); state.diary[0].text='<img src=x onerror="PRIVATE_PAYLOAD()">'
  const ui = await mount(t,{state})
  await ui.click(ui.button('全部回顾'))
  assert.match(ui.query('.ylg-timeline').textContent,/<img src=x/)
  assert.equal(ui.query('.ylg img'),null)
})

test('reset requires explicit phrase, cancel is nonmutating, successful reset checks server', async t => {
  const ui = await mount(t)
  await ui.click(ui.button('数据与隐私'))
  await ui.click(ui.button('清零数据'))
  assert.equal(ui.button('确认清零').disabled,true)
  await ui.click(ui.button('取消'))
  assert.equal(ui.dom.window.document.activeElement,ui.button('清零数据'))
  assert.equal(ui.requests.filter(r => r.options.method === 'POST').length,0)
  await ui.click(ui.button('清零数据'))
  const input = ui.query('.ylg-reset-confirm input')
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(ui.dom.window.HTMLInputElement.prototype,'value').set
    setter.call(input,'清零')
    input.dispatchEvent(new ui.dom.window.Event('input',{bubbles:true}))
    input.dispatchEvent(new ui.dom.window.Event('change',{bubbles:true}))
  })
  assert.equal(ui.button('确认清零').disabled,false)
  await ui.click(ui.button('确认清零'))
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) })
  assert.equal(ui.requests.filter(r => r.options.method === 'POST').length,1)
  assert.equal(ui.requests.find(r => r.options.method === 'POST').options.body,'{"type":"reset"}')
  assert.match(ui.query('[role="status"]').textContent,/已清零/)
  assert.equal(ui.dom.window.document.activeElement,ui.button('清零数据'))
})

test('Escape closes reset confirmation and restores keyboard focus without posting', async t => {
  const ui = await mount(t)
  await ui.click(ui.button('数据与隐私'))
  await ui.click(ui.button('清零数据'))
  const input = ui.query('.ylg-reset-confirm input')
  assert.equal(ui.dom.window.document.activeElement,input)
  await act(async () => input.dispatchEvent(new ui.dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true})))
  assert.equal(ui.query('.ylg-reset-confirm'),null)
  assert.equal(ui.dom.window.document.activeElement,ui.button('清零数据'))
  assert.equal(ui.requests.filter(r => r.options.method === 'POST').length,0)
})

test('real reset timeout never claims success or retries destructive POST automatically', async t => {
  const ui = await mount(t,{fastTimeout:true,responder:async (_path,options) => options.method === 'POST' ? new Promise(() => {}) : {ok:true,json:async () => fixture()}})
  await ui.click(ui.button('数据与隐私'))
  await ui.click(ui.button('清零数据'))
  const input = ui.query('.ylg-reset-confirm input')
  await act(async () => {
    Object.getOwnPropertyDescriptor(ui.dom.window.HTMLInputElement.prototype,'value').set.call(input,'清零')
    input.dispatchEvent(new ui.dom.window.Event('input',{bubbles:true}))
  })
  await ui.click(ui.button('确认清零'))
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 25)) })
  assert.equal(ui.requests.filter(r => r.options.method === 'POST').length,1)
  assert.match(ui.query('.ylg-inline-error').textContent,/尚未获成功确认/)
  assert.doesNotMatch(ui.query('.ylg-notice').textContent,/已清零|已保留/)
  assert.ok(ui.query('.ylg-reset-confirm'))
  await ui.click(ui.button('取消'))
  await ui.click(ui.button('成长概览'))
  assert.equal(ui.query('.ylg-level').textContent,'12')
})

test('protected storage disables destructive action and displays actionable safe warning', async t => {
  const state = fixture(); state.storageHealth={ok:false,error:'SECRET_OWNER_PATH'}
  const ui = await mount(t,{state})
  await ui.click(ui.button('数据与隐私'))
  assert.equal(ui.button('清零数据').disabled,true)
  assert.match(ui.query('.ylg').textContent,/本地档案需要检查/)
  assert.doesNotMatch(ui.query('.ylg').textContent,/SECRET_OWNER_PATH/)
})

test('HTTP failure never shows fabricated stats and retry remains available', async t => {
  const ui = await mount(t,{responder:async () => ({ok:false,status:503})})
  assert.equal(ui.query('.ylg-level'),null)
  assert.match(ui.query('.ylg').textContent,/成长数据暂未就绪/)
  assert.ok(ui.button('重新读取'))
})

test('quick diary writes a local review through the guarded action endpoint', async t => {
  const ui = await mount(t)
  const input = ui.query('#ylg-quick-diary-input')
  assert.ok(input)
  await act(async () => {
    Object.getOwnPropertyDescriptor(ui.dom.window.HTMLTextAreaElement.prototype,'value').set.call(input,'今天一起修好了 bug')
    input.dispatchEvent(new ui.dom.window.Event('input',{bubbles:true}))
    input.dispatchEvent(new ui.dom.window.Event('change',{bubbles:true}))
  })
  await ui.click(ui.button('保存回顾'))
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) })
  const post = ui.requests.find(row => row.options.method === 'POST')
  assert.ok(post)
  assert.equal(post.options.body, JSON.stringify({type:'diary',text:'今天一起修好了 bug'}))
})

test('export button downloads allowlisted summary rather than raw diary/project fields', async t => {
  const state = fixture(); state.topProjects=[['PRIVATE_PROJECT',99]];state.diary[0].text='PRIVATE_DIARY'
  const ui = await mount(t,{state})
  await ui.click(ui.button('数据与隐私'))
  await ui.click(ui.button('导出摘要'))
  assert.equal(ui.downloads.length,1)
  const body = await ui.downloads[0].text()
  assert.doesNotMatch(body,/PRIVATE_PROJECT|PRIVATE_DIARY|topProjects|diary|learned/)
  assert.equal(JSON.parse(body).level,12)
  assert.equal(ui.requests.filter(r => r.options.method === 'POST').length,0)
})
