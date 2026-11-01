#!/usr/bin/env node
// CLI for dsh-dafeiyu-growth.
//
//   node tools/growth.mjs status            → 打印当前成长状态
//   node tools/growth.mjs review "..."      → 记一条成长回顾（agent 写的那种）
//   node tools/growth.mjs reset --confirm   → 备份后清零重来
//   node tools/growth.mjs path              → 打印数据文件位置
//
// review / reset 写进 pending 队列，由正在运行的 Host 立刻取走；Host 没开时
// 会在下次启动时补记。这一点很重要：直接改 profile.json 会被内存里的旧
// 状态覆盖，所以队列才是可靠入口。
import { appendFileSync, existsSync, mkdirSync, readFileSync } from 'node:fs'
import { dirname } from 'node:path'
import { randomUUID } from 'node:crypto'
import { derive, loadProfile } from '../src/growth.js'
import { defaultProfilePath, queuePathForProfile } from '../src/store.js'

const profilePath = defaultProfilePath()
const queuePath = queuePathForProfile(profilePath)

function readProfile() {
  try {
    return loadProfile(JSON.parse(readFileSync(profilePath, 'utf8')))
  } catch (error) {
    if (error.code === 'ENOENT') return loadProfile(undefined)
    console.error(`无法读取成长数据，未修改原文件: ${error.message}`)
    process.exit(1)
  }
}

function enqueue(record) {
  mkdirSync(dirname(queuePath), { recursive: true })
  appendFileSync(queuePath, `${JSON.stringify({ id: randomUUID(), ...record })}\n`, 'utf8')
}

function bar(value, width = 10) {
  const filled = Math.round((Math.max(0, Math.min(100, value)) / 100) * width)
  return `${'#'.repeat(filled)}${'.'.repeat(width - filled)}`
}

const [command = 'status', ...rest] = process.argv.slice(2)

if (command === 'status') {
  const state = derive(readProfile())
  const asJson = rest.includes('--json')
  if (asJson) {
    console.log(JSON.stringify(state, null, 2))
    process.exit(0)
  }
  console.log(`Lv.${state.level} · ${state.stage.name} · ${state.stage.title}`)
  console.log(`经验 ${state.xp}  (${state.into}/${state.need} 到下一级)`)
  console.log(`连续 ${state.streak} 天 · 对话 ${state.counters.turns} · 工具 ${state.counters.toolCalls} · 报错 ${state.counters.toolErrors}`)
  console.log(`体力 ${bar(state.traits.stamina)} ${state.traits.stamina}`)
  console.log(`灵性 ${bar(state.traits.wit)} ${state.traits.wit}`)
  console.log(`亲和 ${bar(state.traits.affinity)} ${state.traits.affinity}`)
  console.log(`定力 ${bar(state.traits.calm)} ${state.traits.calm}`)
  if (state.learned.length) {
    console.log('学到的事：')
    for (const fact of state.learned) console.log(`  · ${fact.text}`)
  }
  if (state.diary.length) {
    console.log('最近回顾：')
    for (const row of state.diary.slice(0, 3)) console.log(`  · [${row.at.slice(0, 16)}] ${row.text}`)
  }
  process.exit(0)
}

if (command === 'review') {
  const text = rest.join(' ').trim()
  if (!text) {
    console.error('用法: node tools/growth.mjs review "回顾内容"')
    process.exit(2)
  }
  enqueue({ type: 'diary', text, at: new Date().toISOString() })
  console.log(`已入队成长回顾（${text.length} 字），Host 运行中会立刻显示`)
  process.exit(0)
}

if (command === 'reset') {
  if (!rest.includes('--confirm')) {
    console.error('重置会清零成长数据；确认后运行: node tools/growth.mjs reset --confirm（Host 会先备份）')
    process.exit(2)
  }
  enqueue({ type: 'reset', confirm: true, at: new Date().toISOString() })
  console.log('已请求重置；Host 运行中会立刻清零')
  process.exit(0)
}

if (command === 'path') {
  console.log(profilePath)
  console.log(`${existsSync(profilePath) ? '已存在' : '尚未创建'}`)
  process.exit(0)
}

console.error(`未知命令: ${command}`)
process.exit(2)
