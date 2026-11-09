import React, {useEffect, useId, useRef, useState} from 'react'
import {exportGrowth} from '../model.js'
import {Button, Icon, Section, formatDate, formatNumber} from './Primitives.jsx'

function SummaryExport({state, onNotice}) {
  const pending = useRef(null)
  useEffect(() => () => { if (pending.current) { clearTimeout(pending.current.timer); URL.revokeObjectURL(pending.current.url) } }, [])
  const download = () => {
    let link
    let url
    try {
      const summary = exportGrowth(state)
      url = URL.createObjectURL(new Blob([JSON.stringify(summary, null, 2)], {type:'application/json'}))
      link = document.createElement('a')
      link.href = url
      link.download = 'yunlan-growth-summary.json'
      document.body.appendChild(link)
      link.click()
      if (pending.current) { clearTimeout(pending.current.timer); URL.revokeObjectURL(pending.current.url) }
      pending.current = {url, timer:setTimeout(() => { URL.revokeObjectURL(url); pending.current = null }, 1000)}
      onNotice('已请求下载统计摘要；请在浏览器下载列表中确认。')
    } catch {
      if (url) URL.revokeObjectURL(url)
      onNotice('导出未完成；没有上传任何数据，请重试或检查浏览器下载权限。')
    } finally { link?.remove() }
  }
  return <div className="ylg-action-row"><div><strong>带走一份成长摘要</strong><p>仅导出等级、计数与属性，不含回顾原文、项目、习惯和会话信息。</p></div><Button icon="download" onClick={download}>导出摘要</Button></div>
}

export function DataPanel({state, busy, reset, actionError, onNotice}) {
  const [confirming, setConfirming] = useState(false)
  const [confirmation, setConfirmation] = useState('')
  const startRef = useRef(null)
  const inputRef = useRef(null)
  const inputId = useId()
  const restoreFocus = useRef(false)
  useEffect(() => {
    if (confirming) inputRef.current?.focus()
    else if (restoreFocus.current) { restoreFocus.current = false; startRef.current?.focus() }
  }, [confirming])
  const cancel = () => { if (busy) return; restoreFocus.current = true; setConfirming(false); setConfirmation('') }
  const submit = async event => {
    event.preventDefault()
    if (busy || confirmation !== '清零') return
    if (await reset()) {
      restoreFocus.current = true
      setConfirming(false); setConfirmation('')
      onNotice('成长统计已清零；服务器已确认保存。')
    }
  }
  const c = state.counters
  const stats = [['累计经验',state.xp], ['会话事件',c.events], ['对话轮次',c.turns], ['成功轮次',c.okTurns],
    ['出错轮次',c.errorTurns], ['工具调用',c.toolCalls], ['工具报错',c.toolErrors], ['错误后恢复',c.recoveries], ['审批次数',c.approvals]]
  return <div className="ylg-data-panel">
    <Section title="数据留在你的设备上" note="只读取本机统计与结构计数，不展示记忆原文、Key 或会话内容">
      <div className="ylg-privacy-message"><Icon name="shield" size={25}/><p>不会因为打开这个页面而上传会话或开启云端模型。桌宠聊天、语音和记忆的权限仍由各自窗口单独管理。</p></div>
      <dl className="ylg-detail-stats">{stats.map(([label,value]) => <div key={label}><dt>{label}</dt><dd>{formatNumber(value)}</dd></div>)}</dl>
      <div className="ylg-date-row"><span>开始记录 · {formatDate(state.createdAt)}</span><span>档案更新 · {formatDate(state.updatedAt, true)}</span></div>
    </Section>
    <Section title="本地数据管理" note="导出只下载到本机；清零不会调用聊天或模型接口">
      <SummaryExport state={state} onNotice={onNotice}/>
      <div className="ylg-danger-zone"><div className="ylg-action-row"><div><strong>重新开始成长记录</strong><p>清空本页的经验、会话统计、属性与回顾。不等于清除桌宠聊天记忆或全部 DSH 会话。</p></div><button type="button" className="ylg-button ylg-button--danger" ref={startRef} disabled={busy || confirming || state.storageHealth.ok === false} onClick={() => setConfirming(true)}>清零数据</button></div>
        {state.storageHealth.ok === false && <p className="ylg-inline-error">档案当前受保护，暂不允许清零。先处理存储问题再重试。</p>}
        {confirming && <form className="ylg-reset-confirm" aria-label="确认清零成长数据" onSubmit={submit} onKeyDown={event => { if (event.key === 'Escape') cancel() }}>
          <label htmlFor={inputId}>此操作会清空本页记录。请输入「清零」确认：</label>
          <input id={inputId} ref={inputRef} autoComplete="off" spellCheck="false" placeholder="输入 清零" value={confirmation} disabled={busy} onChange={event => setConfirmation(event.target.value)}/>
          <div><Button onClick={cancel} disabled={busy}>取消</Button><button type="submit" className="ylg-button ylg-button--danger" disabled={busy || confirmation !== '清零'}>{busy ? '正在清零…':'确认清零'}</button></div>
        </form>}
        {actionError && <p className="ylg-inline-error" role="alert">{actionError}</p>}
      </div>
    </Section>
  </div>
}
