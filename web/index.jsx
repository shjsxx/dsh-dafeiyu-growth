import React from 'react'
import {SettingsDashboard} from './App.jsx'

export function apply(ctx) {
  // Preserve the host slot contract; no shell overlay or global event handlers.
  for (const slot of ['plugins.bundle.config', 'settings.plugins.tab', 'settings.plugin.item']) {
    ctx.slots.inject(slot, () => {
      ctx.slots.register({name:slot, key:'dsh-dafeiyu-growth', id:'dsh-dafeiyu-growth', order:31,
        label:() => '云岚养成'}, SettingsDashboard)
    })
  }
}

export const name = 'dsh-dafeiyu-growth-client'
export const inject = ['slots']
