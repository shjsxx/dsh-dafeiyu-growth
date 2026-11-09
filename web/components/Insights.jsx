import React, {useState} from 'react'
import {Button, EmptyState, Icon, Section, formatDate, formatNumber} from './Primitives.jsx'

const memoryKinds = [
  ['preference', '偏好', 'rose'],
  ['habit', '习惯', 'amber'],
  ['diary', '回顾', 'teal'],
  ['public', '公开知识', 'violet'],
  ['culture', '诗文', 'violet'],
]

function MiniStat({icon, label, value, note, text}) {
  return <div className="ylg-mini-stat">
    <span className="ylg-mini-icon"><Icon name={icon} size={15}/></span>
    <div><span>{label}</span><strong>{text || formatNumber(value)}</strong>{note && <small>{note}</small>}</div>
  </div>
}

function MemoryBars({memory}) {
  const total = Math.max(1, memory.total)
  return <div className="ylg-memory-bars">
    {memoryKinds.map(([key, label, tone]) => {
      const value = memory[key] || 0
      return <div className={`ylg-memory-row ylg-tone-${tone}`} key={key}>
        <span>{label}</span>
        <div className="ylg-memory-track"><span style={{width: `${Math.round(value / total * 100)}%`}}/></div>
        <strong>{formatNumber(value)}</strong>
      </div>
    })}
  </div>
}

function WebList({web}) {
  if (!web.recent.length) return <EmptyState icon="book" title="网页知识还在等第一批内容">打开自选网站学习，或让云岚从公开目录里自己找一条。</EmptyState>
  return <ul className="ylg-web-list">{web.recent.map((row, index) => <li key={`${row.title}-${index}`}>
    <span className="ylg-web-icon"><Icon name="spark" size={14}/></span>
    <div><strong>{row.title}</strong><small>{row.host || '公开来源'}{row.at ? ` · ${formatDate(row.at)}` : ''}</small></div>
  </li>)}</ul>
}

function CloudLine({cloud}) {
  const configured = cloud.configured
  return <div className={`ylg-cloud-line ${configured ? 'is-on' : ''}`}>
    <span className="ylg-cloud-dot"/>
    <div><strong>{configured ? (cloud.model || '云端模型') : '云端未配置'}</strong>
      <small>{configured ? `输出上限 ${formatNumber(cloud.maxTokens)} tokens · ${cloud.prefer ? '优先云端' : '按窗口开关'}` : '本地规则对话仍可用；在聊天设置中单独配置。'}</small>
    </div>
    <span className="ylg-cloud-badge">{configured ? '已配置' : '本地优先'}</span>
  </div>
}

export function MemorySummary({insights, onMore}) {
  const memory = insights.memory
  return <Section title="她记得的" note="只在本机保存；这里只显示分类和计数" action={<Button className="ylg-text-button" icon="arrow" onClick={onMore}>记忆详情</Button>}>
    <div className="ylg-mini-grid">
      <MiniStat icon="heart" label="私人记忆" value={memory.private}/>
      <MiniStat icon="spark" label="偏好与习惯" value={memory.preference + memory.habit}/>
      <MiniStat icon="chat" label="关系话题" value={insights.relationship.topics}/>
    </div>
    {memory.total ? <MemoryBars memory={memory}/> : <EmptyState icon="heart" title="还没有长期记忆">聊几次之后，称呼、偏好和最近一起做的事会慢慢留下来。</EmptyState>}
  </Section>
}

export function LearningSummary({insights, onMore}) {
  const web = insights.web
  const auto = insights.autoLearn
  return <Section title="她最近在学" note="自主选源只读公开页面，不抓取你的私人内容" action={<Button className="ylg-text-button" icon="arrow" onClick={onMore}>学习详情</Button>}>
    <div className="ylg-mini-grid">
      <MiniStat icon="book" label="网页知识" value={web.total} note={`上限 ${formatNumber(web.max)}`}/>
      <MiniStat icon="cloud" label="来源站点" value={web.hosts}/>
      <MiniStat icon="clock" label="自主选源" value={auto.enabled ? auto.interval : 0} note={auto.enabled ? '分钟/轮' : '已暂停'}/>
    </div>
    {web.total ? <div className="ylg-web-count"><div className="ylg-web-track"><span style={{width: `${Math.min(100, Math.round(web.total / web.max * 100))}%`}}/></div><small>{formatNumber(web.total)} / {formatNumber(web.max)} 条</small></div> : <EmptyState icon="leaf" title="还没有网页知识">她会在公开目录里选题，经过训练与情感记录后才写入。</EmptyState>}
  </Section>
}

export function CloudSummary({insights, onMore}) {
  const cloud = insights.cloud
  return <Section title="她和云端的边界" note="云端、记忆和自主选源分别开关，不静默继承" action={<Button className="ylg-text-button" icon="arrow" onClick={onMore}>隐私与设置</Button>}>
    <CloudLine cloud={cloud}/>
    <div className="ylg-boundary-list">
      <span><Icon name="shield" size={14}/> 记忆默认只在本机</span>
      <span><Icon name="leaf" size={14}/> 公开知识不读取私人会话</span>
    </div>
  </Section>
}

export function QuickDiary({onSave, busy}) {
  const [text, setText] = useState('')
  const [saving, setSaving] = useState(false)
  const [note, setNote] = useState('')
  const submit = async event => {
    event.preventDefault()
    if (saving || busy || !text.trim()) return
    setSaving(true)
    const ok = await onSave(text.trim())
    setSaving(false)
    if (ok) { setText(''); setNote('已写入本机成长回顾。') }
    else setNote('没有保存成功，请稍后重试。')
  }
  return <Section title="今天留下点什么" note="一句就够；保存到本机回顾，不发送给模型">
    <form className="ylg-quick-diary" onSubmit={submit}>
      <label htmlFor="ylg-quick-diary-input">给今天的云岚写一句话</label>
      <textarea id="ylg-quick-diary-input" maxLength={400} rows={2} value={text} disabled={saving || busy}
        onChange={event => setText(event.target.value)} placeholder="例如：今天一起把那个 bug 修好了。"/>
      <div className="ylg-quick-row"><span>{text.length}/400</span><Button type="submit" variant="primary" disabled={saving || busy || !text.trim()}>{saving ? '保存中…' : '保存回顾'}</Button></div>
    </form>
    {note && <p className="ylg-inline-note" role="status">{note}</p>}
  </Section>
}

export function MemoryPanel({insights}) {
  const memory = insights.memory
  return <div className="ylg-detail-panel">
    <Section title="记忆结构" note="分类计数来自本地 memory.json；不展示原文">
      <div className="ylg-mini-grid">
        <MiniStat icon="heart" label="私人记忆" value={memory.private}/>
        <MiniStat icon="spark" label="偏好" value={memory.preference}/>
        <MiniStat icon="leaf" label="习惯" value={memory.habit}/>
        <MiniStat icon="book" label="回顾" value={memory.diary}/>
        <MiniStat icon="cloud" label="公开知识" value={memory.public}/>
      </div>
      <MemoryBars memory={memory}/>
    </Section>
    <Section title="她学到的公开网页" note={`${formatNumber(insights.web.total)} 条 · ${formatNumber(insights.web.max)} 上限`}>
      <WebList web={insights.web}/>
    </Section>
    <Section title="关系连续性" note="只显示话题/片段数量，不展示聊天原文">
      <div className="ylg-mini-grid">
        <MiniStat icon="chat" label="关系话题" value={insights.relationship.topics}/>
        <MiniStat icon="book" label="关系片段" value={insights.relationship.episodes}/>
        <MiniStat icon="spark" label="情感网页记录" value={insights.emotion.webLearning}/>
      </div>
      <p className="ylg-panel-note"><Icon name="shield" size={14}/> 记忆可以清除；云端授权只对本次窗口有效。</p>
    </Section>
  </div>
}

export function LearningPanel({insights}) {
  const web = insights.web
  const auto = insights.autoLearn
  return <div className="ylg-detail-panel">
    <Section title="独立网页知识" note="与私人记忆分开，满容量时淘汰低质量旧网页">
      <div className="ylg-mini-grid">
        <MiniStat icon="book" label="当前知识" value={web.total} note={`/ ${formatNumber(web.max)}`}/>
        <MiniStat icon="cloud" label="来源站点" value={web.hosts}/>
        <MiniStat icon="leaf" label="自主来源" value={insights.sources.sites}/>
      </div>
      <div className="ylg-memory-bars"><div className="ylg-memory-row ylg-tone-teal"><span>容量</span><div className="ylg-memory-track"><span style={{width: `${Math.min(100, Math.round(web.total / web.max * 100))}%`}}/></div><strong>{Math.min(100, Math.round(web.total / web.max * 100))}%</strong></div></div>
    </Section>
    <Section title="最近写入" note="只读公开来源，不代表全部互联网">
      <WebList web={web}/>
    </Section>
    <Section title="自主选源" note="公开 RSS/页面，不下载视频，不绕过 robots.txt">
      <div className="ylg-cloud-line is-on"><span className="ylg-cloud-dot"/><div><strong>{auto.enabled ? '正在按计划学习' : '当前暂停'}</strong><small>{auto.enabled ? `每 ${auto.interval} 分钟最多 ${auto.batch} 条 · 训练/情感双记录` : '可在桌面助手或设置中重新开启。'}</small></div><span className="ylg-cloud-badge">{auto.enabled ? '已开启' : '已暂停'}</span></div>
      <div className="ylg-mini-grid"><MiniStat icon="cloud" label="云端模型" value={0} text={insights.cloud.configured ? insights.cloud.model || '已配置' : '未配置'}/><MiniStat icon="clock" label="每轮最多" value={auto.batch}/></div>
    </Section>
  </div>
}
