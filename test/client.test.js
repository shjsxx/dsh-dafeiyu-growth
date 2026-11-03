import test from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {loadClient, source, settingsSlots} from './helpers/load-client.js'

test('client preserves settings slot contracts with only external React and no shell overlay', () => {
  const {client,registrations,injections} = loadClient({fetch() { throw Error('module import must not fetch') }})
  assert.equal(client.name,'dsh-dafeiyu-growth-client')
  assert.deepEqual(Array.from(client.inject),['slots'])
  assert.deepEqual(injections,settingsSlots)
  assert.deepEqual(registrations.map(row => row.options.name),settingsSlots)
  assert.ok(registrations.every(row => row.component === registrations[0].component))
  assert.ok(registrations.every(row => row.options.label() === '云岚养成'))
  assert.ok(!injections.includes('shell.overlay'))
})

test('initial render is scoped accessible skeleton, never fake user statistics', () => {
  const {registrations} = loadClient()
  const html = renderToStaticMarkup(React.createElement(registrations[0].component))
  assert.match(html,/云岚成长仪表盘/)
  assert.match(html,/data-ui-version="0.28.0"/)
  assert.match(html,/正在读取成长数据/)
  assert.match(html,/class="ylg"/)
  assert.doesNotMatch(html,/5417|12,805|Lv\.18/)
  assert.doesNotMatch(source,/dangerouslySetInnerHTML|shell\.overlay|https?:\/\//)
})
