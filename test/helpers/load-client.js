import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {runInNewContext} from 'node:vm'
import React from 'react'

export const source = readFileSync(new URL('../../lib/client.js', import.meta.url), 'utf8')
export const settingsSlots = ['plugins.bundle.config','settings.plugins.tab','settings.plugin.item']
export function loadClient(extra = {}) {
  const registrations = [], injections = []
  let client
  const context = {
    window:{__ModuleLoader__:{load({id,factory}) {
      assert.equal(id,'dsh-dafeiyu-growth')
      client = factory(name => { assert.equal(name,'react'); return React })
    }}},
    console, setTimeout, clearTimeout, AbortController, Blob, URL, ...extra,
  }
  runInNewContext(source, context, {filename:'lib/client.js'})
  client.apply({slots:{inject(slot,callback) { injections.push(slot); callback() },
    register(options,component) { registrations.push({options,component}) }}})
  return {client, registrations, injections, context}
}
