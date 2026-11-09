import React, {useId, useRef, useState} from 'react'
import {useGrowthDashboard} from './hooks.js'
import {Button, Icon, formatDate} from './components/Primitives.jsx'
import {Overview} from './components/Overview.jsx'
import {Journal} from './components/Journal.jsx'
import {DataPanel} from './components/DataPanel.jsx'
import {LearningPanel, MemoryPanel} from './components/Insights.jsx'
import styles from './styles.css'

const tabs = [['overview','成长概览'], ['memory','记忆'], ['learning','学习'], ['journal','成长回顾'], ['data','数据与隐私']]

function Header({refresh, loading, refreshing, busy, state}) {
  return <header className="ylg-header"><div><div className="ylg-brand"><span className="ylg-brand-mark"><Icon name="cloud" size={20}/></span><span>云岚 <i>/</i> 成长手记</span></div><h2>一起，慢慢长大。</h2><p>把每一次真实的同行，留成看得见的成长。</p></div><div className="ylg-header-actions"><span className={`ylg-local-pill ${state?.storageHealth.ok === false ? 'is-warning':''}`}><span/>{state?.storageHealth.ok === false ? '存储需检查':'仅本地记录'}</span><Button icon="refresh" className={refreshing ? 'is-refreshing':''} disabled={loading || refreshing || busy} onClick={() => void refresh()} aria-label={refreshing ? '正在刷新成长数据':'刷新成长数据'}>{refreshing ? '刷新中':'刷新'}</Button></div></header>
}

function Loading() {
  return <div className="ylg-loading" role="status" aria-label="正在读取成长数据"><div className="ylg-skeleton ylg-skeleton-hero"/><div className="ylg-skeleton-row"><div className="ylg-skeleton"/><div className="ylg-skeleton"/><div className="ylg-skeleton"/></div><p>正在打开云岚的成长手记…</p></div>
}

export function SettingsDashboard() {
  const {state, loading, refreshing, busy, error, actionError, lastUpdated, refresh, reset, addDiary} = useGrowthDashboard()
  const [selected, setSelected] = useState('overview')
  const [notice, setNotice] = useState('')
  const id = useId()
  const tabRefs = useRef({})
  const choose = key => { setSelected(key); setNotice('') }
  const keyNav = event => {
    if (busy) return
    const index = tabs.findIndex(([key]) => key === selected)
    const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : -1
    if (next >= 0) { event.preventDefault(); choose(tabs[next][0]); tabRefs.current[tabs[next][0]]?.focus() }
  }
  return <section className="ylg" aria-label="云岚成长仪表盘" data-ui-version="0.28.0">
    <style>{styles}</style>
    <div className="ylg-inner"><Header refresh={refresh} loading={loading && !state} refreshing={refreshing} busy={busy} state={state}/>
      {error && <div className="ylg-banner ylg-banner--warning" role="alert"><Icon name="alert"/><div><strong>{state ? '暂时没有取得最新数据':'成长数据暂未就绪'}</strong><p>{error}{state ? ' 下方保留上次成功读取的记录。':''}</p></div><Button onClick={() => void refresh()} disabled={refreshing}>重试</Button></div>}
      {state?.storageHealth.ok === false && <div className="ylg-banner ylg-banner--warning" role="alert"><Icon name="shield"/><div><strong>本地档案需要检查</strong><p>{state.storageHealth.error || '当前数据可能尚未成功写入。为保护原档案，清零操作已停用。'}</p></div></div>}
      {loading && !state ? <Loading/> : !state ? <div className="ylg-no-data"><Icon name="cloud" size={36}/><h3>稍等一下，云还在路上</h3><p>请确认插件已启用，然后重新读取。不会用示例数字替代你的记录。</p><Button icon="refresh" onClick={() => void refresh()} disabled={refreshing}>重新读取</Button></div> : <>
        <nav className="ylg-tabs" role="tablist" aria-label="成长内容" onKeyDown={keyNav}>{tabs.map(([key,label]) => <button ref={element => { tabRefs.current[key] = element }} type="button" role="tab" key={key} id={`${id}-tab-${key}`} aria-controls={`${id}-panel-${key}`} aria-selected={selected === key} tabIndex={selected === key ? 0 : -1} disabled={busy} onClick={() => choose(key)}>{label}{key === 'journal' && state.diary.length > 0 && <span className="ylg-tab-count">{state.diary.length}</span>}</button>)}</nav>
        {tabs.map(([key]) => <div role="tabpanel" tabIndex={0} key={key} id={`${id}-panel-${key}`} aria-labelledby={`${id}-tab-${key}`} hidden={selected !== key} className="ylg-tabpanel">
          {selected === key && (key === 'overview'
            ? <Overview state={state} busy={busy} onSaveDiary={addDiary} onJournal={() => { choose('journal'); tabRefs.current.journal?.focus() }} onMemory={() => { choose('memory'); tabRefs.current.memory?.focus() }} onLearning={() => { choose('learning'); tabRefs.current.learning?.focus() }} onData={() => { choose('data'); tabRefs.current.data?.focus() }}/>
            : key === 'memory' ? <MemoryPanel insights={state.insights}/>
            : key === 'learning' ? <LearningPanel insights={state.insights}/>
            : key === 'journal' ? <Journal diary={state.diary}/>
            : <DataPanel state={state} busy={busy} reset={reset} actionError={actionError} onNotice={setNotice}/>)}
        </div>)}
        <div className="ylg-notice" role="status" aria-live="polite">{notice}</div>
        <footer className="ylg-footer"><span><Icon name="shield" size={13}/> 本地统计 · 非通用模型训练</span><span>{refreshing ? '正在同步本机记录…' : lastUpdated ? `读取于 ${formatDate(new Date(lastUpdated).toISOString(), true)}` : '等待本机数据'}</span></footer>
      </>}
    </div>
  </section>
}
