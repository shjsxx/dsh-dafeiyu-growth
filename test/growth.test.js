import test from 'node:test'
import assert from 'node:assert/strict'
import {
  addDiary, derive, emptyProfile, levelFromXp, levelProgress, observe, recomputeLearned, resetProfile, stageOf, traitsOf, xpForLevel,
} from '../src/growth.js'

const DAY = 24 * 60 * 60 * 1000
const base = Date.parse('2026-10-30T12:00:00+08:00')

test('level curve is monotonic and reversible', () => {
  assert.equal(levelFromXp(0), 1)
  assert.equal(levelFromXp(39), 1)
  assert.equal(levelFromXp(40), 2)
  assert.equal(levelFromXp(xpForLevel(10)), 10)
  assert.equal(levelProgress(0).ratio, 0)
  assert.ok(levelProgress(50).ratio > 0 && levelProgress(50).ratio < 1)
})

test('stages climb with level', () => {
  assert.equal(stageOf(1).key, 'egg')
  assert.equal(stageOf(5).key, 'fry')
  assert.equal(stageOf(10).key, 'fish')
  assert.equal(stageOf(20).key, 'big')
  assert.equal(stageOf(35).key, 'whale')
})

test('tool calls and completed turns earn xp', () => {
  let profile = emptyProfile(base)
  const session = { id: 's1', cwd: 'C:\\work\\demo' }
  observe(profile, { event: { type: 'tool/call', data: { name: 'read' } }, session, now: base })
  observe(profile, { event: { type: 'tool/call', data: { name: 'read' } }, session, now: base + 1000 })
  observe(profile, { event: { type: 'turn/start', data: {} }, session, now: base + 2000 })
  observe(profile, { event: { type: 'turn/end', data: { reason: { kind: 'completed' } } }, session, now: base + 65000, turnStart: base + 2000 })
  assert.equal(profile.counters.toolCalls, 2)
  assert.equal(profile.tools.read, 2)
  assert.equal(profile.counters.okTurns, 1)
  assert.ok(profile.xp >= 34, `expected >=34 xp, got ${profile.xp}`)
  assert.ok(profile.activeMs >= 60000)
  assert.ok(profile.projects.demo >= 1)
})

test('tool errors raise the error counter and recovery rewards a later success', () => {
  const profile = emptyProfile(base)
  const session = { id: 's2' }
  observe(profile, { event: { type: 'tool/result', data: { error: { code: 'E1' } } }, session, now: base })
  assert.equal(profile.counters.toolErrors, 1)
  assert.equal(profile.errorStreak, 1)
  observe(profile, { event: { type: 'tool/result', data: {} }, session, now: base + 1000 })
  assert.equal(profile.counters.recoveries, 1)
  assert.equal(profile.errorStreak, 0)
})

test('daily streak counts consecutive days', () => {
  const profile = emptyProfile(base)
  for (const offset of [0, 1, 2]) {
    observe(profile, { event: { type: 'tool/call', data: { name: 'read' } }, session: { id: 'x' }, now: base - offset * DAY })
  }
  assert.equal(derive(profile, base).streak, 3)
})

test('learned facts appear once enough evidence exists', () => {
  const profile = emptyProfile(base)
  for (let i = 0; i < 40; i += 1) {
    observe(profile, {
      event: { type: 'tool/call', data: { name: i % 2 ? 'read' : 'bash' } },
      session: { id: 's', cwd: 'C:\\work\\fish' },
      now: base - i * 1000,
    })
  }
  const ids = recomputeLearned(profile, base).map((id) => id)
  assert.ok(ids.includes('tool'), 'tool fact expected')
  assert.ok(ids.includes('hour'), 'hour fact expected')
  assert.ok(ids.includes('project'), 'project fact expected')
  assert.ok(profile.learned.length <= 12)
})

test('traits stay inside 0..100', () => {
  const profile = emptyProfile(base)
  for (let i = 0; i < 400; i += 1) {
    observe(profile, { event: { type: 'tool/call', data: { name: `t${i % 12}` } }, session: { id: 's', cwd: `C:\\w\\p${i % 5}` }, now: base })
  }
  const traits = traitsOf(profile)
  for (const value of Object.values(traits)) {
    assert.ok(value >= 0 && value <= 100, `trait out of range: ${value}`)
  }
})

test('diary keeps newest first and reset clears everything', () => {
  let profile = emptyProfile(base)
  addDiary(profile, '第一条', base)
  addDiary(profile, '第二条', base + 1000)
  assert.equal(profile.diary[0].text, '第二条')
  assert.equal(profile.diary.length, 2)
  assert.ok(profile.lastReviewAt)
  profile = resetProfile(base)
  assert.equal(profile.xp, 0)
  assert.equal(profile.diary.length, 0)
  assert.equal(profile.counters.events, 0)
})
