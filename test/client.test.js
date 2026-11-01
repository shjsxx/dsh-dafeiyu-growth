import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'

const source = readFileSync(new URL('../lib/client.js', import.meta.url), 'utf8')
const settingsSlots = ['plugins.bundle.config', 'settings.plugins.tab', 'settings.plugin.item']

function loadClient(state = null) {
  const injections = []
  const registrations = []
  const errors = []
  const effects = []
  let client
  const React = {
    createElement: (type, props, ...children) => ({ type, props, children }),
    useCallback: (callback) => callback,
    useEffect: (effect) => { effects.push(effect) },
    useState: (initial) => [initial === null ? state : initial, () => {}],
  }
  runInNewContext(source, {
    window: { __ModuleLoader__: { load({ id, factory }) {
      assert.equal(id, 'dsh-dafeiyu-growth')
      client = factory((name) => {
        assert.equal(name, 'react')
        return React
      })
    } } },
    console: { error: (...args) => errors.push(args) },
  }, { filename: 'lib/client.js' })
  client.apply({ slots: {
    inject(slot, callback) {
      injections.push(slot)
      callback()
    },
    register(options, component) { registrations.push({ options, component }) },
  } })
  assert.deepEqual(errors, [], 'registration errors must not be swallowed')
  return { client, injections, registrations, effects }
}

test('client registers settings cards without injecting or registering a shell overlay', () => {
  const { client, injections, registrations, effects } = loadClient()
  assert.equal(client.name, 'dsh-dafeiyu-growth-client')
  assert.deepEqual(Array.from(client.inject), ['slots'])
  assert.equal(injections.includes('shell.overlay'), false)
  assert.deepEqual(injections, settingsSlots)
  assert.equal(registrations.some(({ options }) => options.name === 'shell.overlay'), false)
  assert.deepEqual(registrations.map(({ options }) => options.name), settingsSlots)
  const component = registrations[0].component
  for (const registration of registrations) {
    assert.equal(registration.component, component)
    assert.equal(registration.options.id, 'dsh-dafeiyu-growth')
    assert.equal(registration.options.label(), '云岚养成')
  }
  const card = component()
  assert.equal(card.type, 'ul')
  assert.equal(card.children[0].children[0], '正在读取成长数据…')
  assert.equal(effects.length, 1, 'settings card retains its growth data hook')
})

test('registered settings card retains growth stats and Traits rendering', () => {
  const traits = { stamina: 20, wit: 30, affinity: 40, calm: 50 }
  const { registrations } = loadClient({
    level: 2, stage: { name: '云芽' }, xp: 45, into: 5, need: 60, streak: 3,
    counters: { turns: 2, okTurns: 2, errorTurns: 0, toolCalls: 5, toolErrors: 0, recoveries: 0, approvals: 0 },
    traits, learned: [], diary: [],
  })
  const card = registrations[0].component()
  assert.equal(card.type, 'ul')
  assert.match(JSON.stringify(card), /Lv\.2 · 云芽/)
  const traitsElement = card.children[2].children[1]
  assert.equal(typeof traitsElement.type, 'function')
  assert.equal(traitsElement.props.traits, traits)
  const renderedTraits = traitsElement.type(traitsElement.props)
  assert.equal(renderedTraits.children.length, 4)
  assert.deepEqual(renderedTraits.children.map((row) => row.children[0].children[0]), ['体力', '灵性', '亲和', '定力'])
})
