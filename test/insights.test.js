import test from 'node:test'
import assert from 'node:assert/strict'
import {mkdirSync, writeFileSync, rmSync} from 'node:fs'
import {join} from 'node:path'
import {tmpdir} from 'node:os'
import {collectInsights} from '../src/insights.js'

test('dashboard insights expose counts and public titles only', () => {
  const dir = join(tmpdir(), `yunlan-insights-${process.pid}-${Date.now()}`)
  mkdirSync(dir, {recursive: true})
  try {
    writeFileSync(join(dir, 'memory.json'), JSON.stringify({records: [
      {kind: 'preference', text: 'PRIVATE_PREFERENCE'},
      {kind: 'habit', text: 'PRIVATE_HABIT'},
      {kind: 'public', text: 'PRIVATE_WEB_TEXT'},
    ]}))
    writeFileSync(join(dir, 'web-knowledge.json'), JSON.stringify({maxRecords: 2000, records: [
      {title: 'Public title', host: 'example.com', text: 'PRIVATE_BODY', fetchedAt: 1700000000},
    ]}))
    writeFileSync(join(dir, 'cloud.json'), JSON.stringify({model: 'deepseek-chat', max_tokens: 800,
      prefer_cloud: true, proactive_smart: true, key_protected: 'SECRET_KEY'}))
    writeFileSync(join(dir, 'relationship.json'), JSON.stringify({topics: [{text: 'PRIVATE_TOPIC'}], episodes: [{text: 'PRIVATE_EPISODE'}]}))
    writeFileSync(join(dir, 'emotion.json'), JSON.stringify({mood: 'neutral', webLearning: [{host: 'example.com'}]}))
    writeFileSync(join(dir, 'auto-learning.json'), JSON.stringify({enabled: true, interval: 15, batch: 3}))
    writeFileSync(join(dir, 'web-sources.json'), JSON.stringify({sites: [{host: 'example.com'}]}))
    const data = collectInsights(dir)
    const serialized = JSON.stringify(data)
    assert.equal(data.memory.total, 3)
    assert.equal(data.memory.private, 2)
    assert.equal(data.memory.preference, 1)
    assert.equal(data.web.total, 1)
    assert.equal(data.web.recent[0].title, 'Public title')
    assert.equal(data.cloud.model, 'deepseek-chat')
    assert.equal(data.cloud.configured, true)
    assert.equal(data.relationship.topics, 1)
    assert.doesNotMatch(serialized, /PRIVATE_|SECRET_KEY/)
  } finally {
    rmSync(dir, {recursive: true, force: true})
  }
})
