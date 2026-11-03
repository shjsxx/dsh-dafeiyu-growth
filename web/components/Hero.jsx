import React from 'react'
import {Icon, formatNumber} from './Primitives.jsx'
import {STAGES} from '../model.js'
const stages = STAGES.map(stage => ({name:stage.name, level:stage.minLevel}))

function CloudIllustration() {
  return <svg className="ylg-cloud-art" viewBox="0 0 180 155" fill="none" aria-hidden="true" focusable="false">
    <circle cx="100" cy="78" r="61" fill="currentColor" opacity=".06"/>
    <circle cx="132" cy="36" r="15" fill="#D6B676" opacity=".72"/>
    <path d="M36 94c-20-23 8-47 28-37 4-35 54-40 65-4 29-1 35 35 11 43-24 7-75 7-104-2Z" fill="var(--yl-art,#fff)" stroke="currentColor" strokeWidth="1.7"/>
    <path d="M74 77v3m29-3v3m-20 6c3 3 8 3 11-1" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round"/>
    <ellipse cx="69" cy="85" rx="5" ry="2.5" fill="#D6B4A0" opacity=".55"/>
    <ellipse cx="110" cy="85" rx="5" ry="2.5" fill="#D6B4A0" opacity=".55"/>
    <path d="M27 115h91c19 0 18 17 5 17m-79-7h43M145 108h13m-16-8h22" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" opacity=".48"/>
    <path d="m38 38 2 5 5 2-5 2-2 5-2-5-5-2 5-2 2-5Zm106 31 1 3 3 1-3 1-1 3-1-3-3-1 3-1 1-3Z" fill="currentColor" opacity=".5"/>
  </svg>
}

export function GrowthHero({state}) {
  const currentStage = stages.reduce((index, stage, i) => state.level >= stage.level ? i : index, 0)
  const remaining = Math.max(0, state.need - state.into)
  const maxLevel = state.level >= 60
  return <section className="ylg-hero" aria-label="等级与成长进度">
    <div className="ylg-hero-top"><div className="ylg-hero-copy">
      <div className="ylg-eyebrow">每一次同行，都算数</div>
      <div className="ylg-level-line"><span className="ylg-level-prefix">Lv.</span><strong className="ylg-level">{state.level}</strong><span className="ylg-stage-badge">{state.stage.name}</span></div>
      <p className="ylg-stage-title">{state.stage.title || '一缕云气，正在慢慢长大'}</p>
    </div><CloudIllustration/></div>
    <div className="ylg-progress-heading"><span>{maxLevel ? '已达到当前最高等级' : <>距离 Lv.{state.level + 1} 还差 <strong>{formatNumber(remaining)}</strong> 经验</>}</span><span>{formatNumber(state.into)} <i>/</i> {formatNumber(state.need)}</span></div>
    <div className="ylg-progress" role="progressbar" aria-label="当前等级经验进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={state.percent} aria-valuetext={`${state.into} / ${state.need} 经验`}><span style={{width:`${state.percent}%`}}/></div>
    <ol className="ylg-stages" aria-label="成长阶段">
      {stages.map((stage, index) => <li key={stage.name} className={index === currentStage ? 'is-current' : index < currentStage ? 'is-past' : ''} aria-current={index === currentStage ? 'step' : undefined}>
        <span className="ylg-stage-dot">{index < currentStage ? <Icon name="check" size={10}/> : <span/>}</span>
        <span>{stage.name}</span><small>Lv.{stage.level}</small>
      </li>)}
    </ol>
  </section>
}
