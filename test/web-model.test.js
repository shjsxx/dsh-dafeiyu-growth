import test from 'node:test'
import assert from 'node:assert/strict'
import { normalizeGrowth, normalizeTimestamp, createDashboardModel, exportGrowth } from '../web/model.js'

test('empty and malformed server views are bounded and renderable', () => {
  for (const raw of [null, [], '', { level: Infinity, xp: NaN, ratio: -7, traits: { stamina: Infinity, calm: -2 }, counters: { turns: -5 }, diary: [{ text: 123 }, null], learned: 'bad', stage: [] }]) {
    const model = normalizeGrowth(raw)
    assert.equal(model.level, 1)
    assert.equal(model.xp, 0)
    assert.equal(model.percent, 0)
    assert.equal(model.counters.turns, 0)
    assert.deepEqual(model.diary, [])
    assert.equal(model.storageHealth.ok, null)
  }
})
test('all percentages, counts, legacy stage names and strings normalize', () => {
  const model = normalizeGrowth({ level: 999, xp: 1e50, ratio: 4, into: 9, need: 4, streak: 9999,
    stage: { key: 'egg', name: 'Egg', accent: 'javascript:bad' }, traits: { wit: 1000, affinity: -1 },
    counters: { turns: 5.9 }, learned: [null, { id: 'tool', text: 'hello\u0000', at: 'invalid' }],
    diary: [{ text: 'x'.repeat(2000), at: '2026-01-02T00:00:00Z', level: 500 }], topTools: [null, ['tool', -1]] })
  assert.equal(model.level, 60)
  assert.equal(model.percent, 100)
  assert.equal(model.into, 4)
  assert.equal(model.xp, Number.MAX_SAFE_INTEGER)
  assert.equal(model.streak, 400)
  assert.equal(model.stage.name, '云芽')
  assert.equal(model.stage.accent, '#a9c6da')
  assert.equal(model.traits.wit, 100)
  assert.equal(model.traits.affinity, 0)
  assert.equal(model.counters.turns, 5)
  assert.equal(model.learned[0].text, 'hello')
  assert.equal(model.learned[0].at, null)
  assert.equal(model.diary[0].text.length, 800)
  assert.equal(model.diary[0].level, 60)
  assert.deepEqual(model.topTools, [['tool', 0]])
})
test('timestamps and health do not leak raw backend paths', () => {
  const model = createDashboardModel({ createdAt: 'bad', updatedAt: Infinity, storageHealth: { ok: false, readable: false, writable: false, error: 'C:/private/id' } }, { now: 0 })
  assert.equal(model.createdAt, null)
  assert.equal(model.updatedAt, null)
  assert.equal(normalizeTimestamp('2026-02-30T00:00:00Z'), null)
  assert.equal(normalizeTimestamp('2026-13-01T00:00:00Z'), null)
  assert.equal(model.ageDays, 0)
  assert.equal(model.storageHealth.ok, false)
  assert.doesNotMatch(model.storageHealth.error, /private/)
  assert.equal(createDashboardModel({ createdAt: '2026-01-01T00:00:00Z' }, { now: Date.parse('2026-01-03T00:00:00Z') }).ageDays, 2)
})
test('export is a strict privacy allowlist even with malicious server extras', () => {
  const result = exportGrowth({ sessionId: 'secret-session', topProjects: [['secret-project', 4]], projects: { secret: 4 }, topTools: [['secret-tool', 8]], learned: [{ id: 'project', text: 'secret project' }], diary: [{ text: 'secret diary' }], storageHealth: { ok: false, error: 'secret path' }, counters: { turns: 4, secret: 5 } })
  assert.doesNotMatch(JSON.stringify(result), /secret/)
  assert.equal(result.counters.turns, 4)
  assert.deepEqual(Object.keys(result), ['version', 'xp', 'level', 'stage', 'streak', 'counters', 'traits', 'createdAt', 'updatedAt'])
})
