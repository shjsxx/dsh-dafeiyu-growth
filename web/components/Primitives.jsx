import React from 'react'

const paths = {
  cloud: 'M7 18a5 5 0 1 1 .8-9.9A6 6 0 0 1 19 11a3.5 3.5 0 0 1-.5 7H7Z',
  refresh: 'M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1',
  leaf: 'M20 4C8 2 3 9 6 15c6 4 13 0 14-11ZM5 20l9-10M9 15l-1-5M12 12l5 1',
  chat: 'M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l1.6-4A8.5 8.5 0 1 1 21 11.5ZM8 10h8M8 14h5',
  tool: 'm14 5 5 5M6 14l4 4M15 3l6 6-10 10-6-6L15 3ZM4 16l-2 6 6-2',
  sun: 'M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4 1.4-1.4M17 7l1.4-1.4M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z',
  spark: 'm12 3 2.2 6.8L21 12l-6.8 2.2L12 21l-2.2-6.8L3 12l6.8-2.2L12 3Z',
  heart: 'M20.8 5.6a5.5 5.5 0 0 0-8.8.8 5.5 5.5 0 0 0-8.8-.8c-4.2 4.6 2 10 8.8 14.4 6.8-4.4 13-9.8 8.8-14.4Z',
  shield: 'm12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Zm-4 9 3 3 5-6',
  arrow: 'M5 12h14m-5-5 5 5-5 5',
  download: 'M12 3v12m-4-4 4 4 4-4M4 17v4h16v-4',
  book: 'M4 4h6a3 3 0 0 1 3 3v14a4 4 0 0 0-4-2H4V4Zm9 3a3 3 0 0 1 3-3h5v15h-4a4 4 0 0 0-4 2',
  alert: 'm12 3 10 18H2L12 3Zm0 6v5m0 3v.1',
  clock: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9-5v5l3 2',
  check: 'm5 12 4 4L19 6',
  dots: 'M5 12h.01M12 12h.01M19 12h.01',
}

export function Icon({name, size=18, ...props}) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false" {...props}><path d={paths[name] || paths.cloud}/></svg>
}

export function Button({children, icon, variant='secondary', className='', ...props}) {
  return <button type="button" className={`ylg-button ylg-button--${variant} ${className}`} {...props}>{icon && <Icon name={icon} size={16}/>}<span>{children}</span></button>
}

export function Section({title, note, action, children, className=''}) {
  return <section className={`ylg-card ${className}`} aria-label={title}>
    <header className="ylg-section-heading"><div><h3>{title}</h3>{note && <p>{note}</p>}</div>{action}</header>
    {children}
  </section>
}

export function EmptyState({icon='leaf', title, children}) {
  return <div className="ylg-empty"><span className="ylg-empty-icon"><Icon name={icon} size={24}/></span><strong>{title}</strong><p>{children}</p></div>
}

export function formatNumber(value) {
  return new Intl.NumberFormat('zh-CN').format(value)
}

export function formatDate(value, withTime=false) {
  const date = typeof value === 'string' ? new Date(value) : null
  if (!date || !Number.isFinite(date.getTime())) return '日期未记录'
  return new Intl.DateTimeFormat('zh-CN', {month:'2-digit', day:'2-digit', ...(withTime ? {hour:'2-digit', minute:'2-digit', hour12:false} : {})}).format(date)
}
