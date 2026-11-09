import React, {useState} from 'react'
import {Button, EmptyState, Icon, Section, formatDate, formatNumber} from './Primitives.jsx'
import {GrowthHero} from './Hero.jsx'
import {CloudSummary, LearningSummary, MemorySummary, QuickDiary} from './Insights.jsx'

function Stat({icon, label, value, note}) {
  return <div className="ylg-stat"><div className="ylg-stat-label"><Icon name={icon} size={15}/><span>{label}</span></div><strong>{formatNumber(value)}</strong><p>{note}</p></div>
}

export function Traits({traits}) {
  const rows = [
    {key:'stamina', name:'体力', icon:'sun', note:'有效会话时长', color:'amber'},
    {key:'wit', name:'灵性', icon:'spark', note:'工具与项目多样性', color:'violet'},
    {key:'affinity', name:'亲和', icon:'heart', note:'对话与审批互动', color:'rose'},
    {key:'calm', name:'定力', icon:'shield', note:'已结束对话的成功占比', color:'teal'},
  ]
  return <div className="ylg-traits">{rows.map(row => <div className={`ylg-trait ylg-tone-${row.color}`} key={row.key}>
    <div className="ylg-trait-heading"><span><Icon name={row.icon} size={16}/>{row.name}</span><strong>{traits[row.key]}<small>/100</small></strong></div>
    <div className="ylg-trait-track" role="progressbar" aria-label={row.name} aria-valuemin={0} aria-valuemax={100} aria-valuenow={traits[row.key]}><span style={{width:`${traits[row.key]}%`}}/></div>
    <p>{row.note}</p>
  </div>)}</div>
}

function Insights({learned}) {
  const [expanded, setExpanded] = useState(false)
  const visible = expanded ? learned : learned.slice(0, 4)
  return <Section title="慢慢了解你" note="来自会话统计的习惯线索，不是模型训练" action={<span className="ylg-count">{learned.length} 条</span>} className="ylg-insights-card">
    {visible.length ? <ul className="ylg-insights">{visible.map((fact, index) => <li key={fact.id + '-' + index}><span className="ylg-insight-icon"><Icon name={index % 2 ? 'leaf':'spark'} size={15}/></span><p>{fact.text}</p></li>)}</ul> : <EmptyState title="还在认识你的路上">积累一些真实会话后，这里会出现你的使用习惯。</EmptyState>}
    {learned.length > 4 && <Button className="ylg-text-button" onClick={() => setExpanded(value => !value)} aria-expanded={expanded}>{expanded ? '收起线索' : `查看其余 ${learned.length - 4} 条`}</Button>}
  </Section>
}

function TodaySignals({state, onMemory, onLearning}) {
  const c = state.counters
  const insights = state.insights
  return <div className="ylg-signals" aria-label="今日状态摘要">
    <button type="button" className="ylg-signal" onClick={() => onMemory?.()}>
      <span className="ylg-signal-icon"><Icon name="heart" size={16}/></span>
      <span><small>她记得</small><strong>{formatNumber(insights.memory.private)}</strong><em>条私人记忆</em></span>
      <Icon name="arrow" size={14}/>
    </button>
    <button type="button" className="ylg-signal" onClick={() => onLearning?.()}>
      <span className="ylg-signal-icon"><Icon name="book" size={16}/></span>
      <span><small>她学过</small><strong>{formatNumber(insights.web.total)}</strong><em>条网页知识</em></span>
      <Icon name="arrow" size={14}/>
    </button>
    <div className="ylg-signal is-static">
      <span className="ylg-signal-icon"><Icon name="chat" size={16}/></span>
      <span><small>一起聊过</small><strong>{formatNumber(c.turns)}</strong><em>轮对话</em></span>
    </div>
    <div className="ylg-signal is-static">
      <span className="ylg-signal-icon"><Icon name="cloud" size={16}/></span>
      <span><small>云端</small><strong>{insights.cloud.configured ? '就绪' : '本地'}</strong><em>{insights.cloud.configured ? (insights.cloud.model || '已配置') : '未配置'}</em></span>
    </div>
  </div>
}

export function Overview({state, onJournal, onMemory, onLearning, onData, onSaveDiary, busy}) {
  const counters = state.counters
  return <div className="ylg-overview">
    <GrowthHero state={state}/>
    <TodaySignals state={state} onMemory={onMemory} onLearning={onLearning}/>
    <QuickDiary onSave={onSaveDiary} busy={busy}/>
    <div className="ylg-dashboard-grid">
      <Section title="四维成长" note="由使用记录映射的数值，不代表真实能力或心理特征"><Traits traits={state.traits}/></Section>
      <MemorySummary insights={state.insights} onMore={onMemory}/>
      <Insights learned={state.learned}/>
      <LearningSummary insights={state.insights} onMore={onLearning}/>
      <CloudSummary insights={state.insights} onMore={onData}/>
      <Section title="最近的一页" note="留住一次成长，而不只是一个数字" action={<Button className="ylg-text-button" icon="arrow" onClick={onJournal}>全部回顾</Button>}>
        {state.diary.length ? <div className="ylg-preview-entry"><span className="ylg-preview-icon"><Icon name={state.diary[0].kind === 'stage' ? 'spark':'book'} size={20}/></span><div><span className="ylg-entry-date">{formatDate(state.diary[0].at, true)} · {state.diary[0].kind === 'stage' ? '阶段成长':'成长记录'}</span><p>{state.diary[0].text}</p></div></div> : <EmptyState icon="book" title="手记还空着">升级与阶段变化会留下记录。你也可以在这里写一条回顾。</EmptyState>}
      </Section>
    </div>
  </div>
}
