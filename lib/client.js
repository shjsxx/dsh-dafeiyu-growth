window.__ModuleLoader__.load({ id: 'dsh-dafeiyu-growth', factory: (require) => {
  const module = { exports: {} }
  const React = require('react')
  const { useCallback, useEffect, useState } = React

  const STATE_ENDPOINT = '/plugins/dsh-dafeiyu-growth/state'
  const ACTION_ENDPOINT = '/plugins/dsh-dafeiyu-growth/action'

  const cardStyle = {
    listStyle: 'none', border: '1px solid var(--border-color, #d8d8d8)', borderRadius: 12,
    padding: 16, background: 'var(--surface-color, transparent)', display: 'grid', gap: 12,
  }
  const rowStyle = { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16 }
  const mutedStyle = { opacity: 0.66, fontSize: 12 }

  // Older profiles still carry the fish-era stage names; show the current ones.
  const LEGACY_STAGE_NAMES = [['鱼卵', '云芽'], ['幼鱼', '听风'], ['小鱼', '御风'], ['大鱼', '踏云']]
  function modernText(value) {
    let text = typeof value === 'string' ? value : ''
    for (const [oldName, newName] of LEGACY_STAGE_NAMES) text = text.split(oldName).join(newName)
    return text.replace(/(?<!岚)鲸/g, '岚鲸')
  }

  function useGrowth(intervalMs) {
    const [state, setState] = useState(null)
    const [error, setError] = useState(null)
    const load = useCallback(() => {
      fetch(STATE_ENDPOINT, { cache: 'no-store' })
        .then((response) => (response.ok ? response.json() : Promise.reject(new Error(String(response.status)))))
        .then((next) => { setState(next); setError(null) })
        .catch((cause) => setError(String(cause?.message ?? cause)))
    }, [])
    useEffect(() => {
      let alive = true
      const tick = () => { if (alive) load() }
      tick()
      const timer = setInterval(tick, intervalMs ?? 15000)
      return () => { alive = false; clearInterval(timer) }
    }, [intervalMs, load])
    return { state, error, reload: load }
  }

  function Traits({ traits }) {
    const rows = [
      ['体力', traits.stamina, '#e0894a'],
      ['灵性', traits.wit, '#7f6bd0'],
      ['亲和', traits.affinity, '#d0568a'],
      ['定力', traits.calm, '#3f9b74'],
    ]
    return React.createElement('div', { style: { display: 'grid', gap: 5 } },
      ...rows.map(([label, value, color]) => React.createElement('div', { key: label, style: { display: 'grid', gridTemplateColumns: '34px 1fr 26px', alignItems: 'center', gap: 6, fontSize: 11 } },
        React.createElement('span', { style: { opacity: 0.75 } }, label),
        React.createElement('span', { style: { height: 5, borderRadius: 3, background: 'rgba(127,127,127,0.22)', overflow: 'hidden' } },
          React.createElement('span', { style: { display: 'block', width: `${Math.max(0, Math.min(100, value))}%`, height: '100%', background: color, borderRadius: 3 } })),
        React.createElement('span', { style: { textAlign: 'right', opacity: 0.7 } }, String(value)),
      )))
  }

  function SettingsCard() {
    const { state, error, reload } = useGrowth(10000)
    const [busy, setBusy] = useState(false)

    const act = async (body) => {
      setBusy(true)
      try {
        await fetch(ACTION_ENDPOINT, {
          method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
        })
        reload()
      } catch {} finally {
        setBusy(false)
      }
    }

    if (!state) {
      return React.createElement('ul', { style: cardStyle },
        React.createElement('li', null, error ? `成长数据未就绪：${error}` : '正在读取成长数据…'))
    }

    const stats = [
      ['等级', `Lv.${state.level} · ${modernText(state.stage.name)}`],
      ['经验', `${state.xp}（${state.into}/${state.need}）`],
      ['连续天数', String(state.streak)],
      ['对话轮次', `${state.counters.turns}（成功 ${state.counters.okTurns} / 出错 ${state.counters.errorTurns}）`],
      ['工具调用', String(state.counters.toolCalls)],
      ['工具报错', `${state.counters.toolErrors}（自救 ${state.counters.recoveries}）`],
      ['审批次数', String(state.counters.approvals)],
    ]

    return React.createElement('ul', { style: cardStyle },
      React.createElement('li', null,
        React.createElement('div', { style: { fontWeight: 700, marginBottom: 4 } }, '养成'),
        React.createElement('div', { style: mutedStyle }, '数据来自真实的 DSH 会话事件，全部保存在本地。')),
      React.createElement('li', null,
        React.createElement('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px 12px' } },
          ...stats.map(([label, value]) => React.createElement('div', { key: label, style: { fontSize: 12 } },
            React.createElement('span', { style: { opacity: 0.62 } }, `${label}：`), value)))),
      React.createElement('li', null,
        React.createElement('div', { style: { fontWeight: 600, marginBottom: 4 } }, '四维属性'),
        React.createElement(Traits, { traits: state.traits })),
      React.createElement('li', null,
        React.createElement('div', { style: { fontWeight: 600, marginBottom: 4 } }, '它学到的事'),
        state.learned.length === 0
          ? React.createElement('div', { style: mutedStyle }, '还不够了解你，多用一会儿就会出现。')
          : React.createElement('div', { style: { display: 'grid', gap: 3 } },
              ...state.learned.map((fact) => React.createElement('div', { key: fact.id, style: { fontSize: 12, ...rowStyle, justifyContent: 'flex-start' } }, '· ' + fact.text)))),
      React.createElement('li', null,
        React.createElement('div', { style: { fontWeight: 600, marginBottom: 4 } }, '成长回顾'),
        state.diary.length === 0
          ? React.createElement('div', { style: mutedStyle }, '还没有回顾。让 agent 用 tools/growth.mjs review 写一条。')
          : React.createElement('div', { style: { display: 'grid', gap: 6 } },
              ...state.diary.slice(0, 5).map((entry, index) => React.createElement('div', { key: `${entry.at}-${index}`, style: { fontSize: 12 } },
                React.createElement('div', { style: mutedStyle }, entry.at.slice(0, 16) + (entry.kind === 'stage' ? ' · 进化' : '')),
                React.createElement('div', null, modernText(entry.text)))))),
      React.createElement('li', { style: rowStyle },
        React.createElement('span', { style: mutedStyle }, busy ? '处理中…' : '操作'),
        React.createElement('span', { style: { display: 'flex', gap: 8 } },
          React.createElement('button', { type: 'button', onClick: () => reload(), style: { padding: '5px 10px', borderRadius: 8 } }, '刷新'),
          React.createElement('button', {
            type: 'button', onClick: () => { if (window.confirm('清零成长数据？经验、属性和回顾都会清空。')) void act({ type: 'reset' }) },
            style: { padding: '5px 10px', borderRadius: 8 },
          }, '清零'),
          React.createElement('button', {
            type: 'button',
            onClick: () => {
              const blob = new Blob([JSON.stringify(state, null, 2)], { type: 'application/json' })
              const url = URL.createObjectURL(blob)
              const link = document.createElement('a')
              link.href = url
              link.download = 'dsh-dafeiyu-growth.json'
              link.click()
              URL.revokeObjectURL(url)
            },
            style: { padding: '5px 10px', borderRadius: 8 },
          }, '导出'))),
    )
  }

  function apply(ctx) {
    const register = (slot, key, component) => () => {
      try {
        ctx.slots.register({ name: slot, key: 'dsh-dafeiyu-growth', id: 'dsh-dafeiyu-growth', order: 31, label: () => '云岚养成' }, component)
      } catch (error) {
        if (typeof console !== 'undefined' && console.error) console.error('[dsh-dafeiyu-growth] register failed:', error)
      }
    }
    for (const slot of ['plugins.bundle.config', 'settings.plugins.tab', 'settings.plugin.item']) {
      try { ctx.slots.inject(slot, register(slot, 'card', SettingsCard)) } catch {}
    }
  }

  module.exports = { name: 'dsh-dafeiyu-growth-client', inject: ['slots'], apply }
  return module.exports
} })
