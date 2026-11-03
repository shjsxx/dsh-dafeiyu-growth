import React, {useState} from 'react'
import {EmptyState, Icon, Section, formatDate} from './Primitives.jsx'

export function Journal({diary}) {
  const [filter, setFilter] = useState('all')
  const rows = diary.filter(row => filter === 'all' || (filter === 'stage' ? row.kind === 'stage' : row.kind !== 'stage'))
  return <Section title="成长回顾" note="保存在本机的最近 20 条记录，不自动上传">
    <div className="ylg-filter" role="group" aria-label="回顾类型">
      {[['all','全部记录'], ['stage','阶段成长'], ['review','日常回顾']].map(([key, label]) => <button type="button" key={key} aria-pressed={filter === key} onClick={() => setFilter(key)}>{label}</button>)}
    </div>
    {rows.length ? <ol className="ylg-timeline">{rows.map((row, index) => <li key={`${row.at}-${index}`}>
      <span className={`ylg-timeline-dot ${row.kind === 'stage' ? 'is-stage':''}`}><Icon name={row.kind === 'stage' ? 'spark':'leaf'} size={15}/></span>
      <div className="ylg-timeline-body"><div className="ylg-timeline-meta"><time dateTime={row.at || undefined}>{formatDate(row.at, true)}</time><span>{row.kind === 'stage' ? '阶段成长':'日常回顾'}</span>{row.level != null && <span>Lv.{row.level}</span>}</div><p>{row.text}</p></div>
    </li>)}</ol> : <EmptyState icon="book" title={filter === 'all' ? '故事才刚刚开始':'这一类还没有记录'}>每一次升级和阶段变化都会留下足迹；已有的回顾不会因切换筛选而丢失。</EmptyState>}
    <p className="ylg-footnote">时间按设备本地时区显示。回顾是记录，不是新的训练样本。</p>
  </Section>
}
