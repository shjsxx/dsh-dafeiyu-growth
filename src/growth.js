// Growth model for the BigFish companion.
//
// Pure functions only: no I/O, no clock reads beyond the injected `now`. The
// server plugin owns persistence and event wiring; everything that can be
// tested lives here.
//
// Honest scope: this is statistical adaptation, not machine learning. The
// profile accumulates counters and simple histograms from real DSH session
// events; "learning" means turning those counts into a few stable statements
// about how the owner works, and "evolution" means thresholds that unlock
// stages, titles and dialogue.

export const PROFILE_VERSION = 1

export const STAGES = [
  { key: 'egg', name: '云芽', minLevel: 1, accent: '#a9c6da', title: '刚凝成的一缕云气' },
  { key: 'fry', name: '听风', minLevel: 5, accent: '#7fa9c9', title: '会追着风跑的小云灵' },
  { key: 'fish', name: '御风', minLevel: 10, accent: '#5c8fbf', title: '能自己穿过云层' },
  { key: 'big', name: '踏云', minLevel: 20, accent: '#3f6fa8', title: '踏着云海走了很远' },
  { key: 'whale', name: '岚鲸', minLevel: 35, accent: '#2b4e7e', title: '云海尽头的鲸' },
]

const XP_PER_LEVEL = 40
const MAX_LEVEL = 60

export function xpForLevel(level) {
  const n = Math.max(1, Math.min(MAX_LEVEL, Math.floor(level)))
  return XP_PER_LEVEL * (n - 1) * (n - 1)
}

export function levelFromXp(xp) {
  const value = Number.isFinite(xp) && xp > 0 ? xp : 0
  const level = Math.floor(Math.sqrt(value / XP_PER_LEVEL)) + 1
  return Math.max(1, Math.min(MAX_LEVEL, level))
}

export function levelProgress(xp) {
  const level = levelFromXp(xp)
  if (level >= MAX_LEVEL) return { level, into: 1, need: 1, ratio: 1, nextXp: xp }
  const cur = xpForLevel(level)
  const next = xpForLevel(level + 1)
  const need = Math.max(1, next - cur)
  const into = Math.max(0, Math.min(need, (xp ?? 0) - cur))
  return { level, into, need, ratio: into / need, nextXp: next }
}

export function stageOf(level) {
  let found = STAGES[0]
  for (const stage of STAGES) if (level >= stage.minLevel) found = stage
  return found
}

function localDateKey(now) {
  const d = now instanceof Date ? now : new Date(now)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function emptyHours() {
  return Array.from({ length: 24 }, () => 0)
}

export function emptyProfile(now = Date.now()) {
  return {
    version: PROFILE_VERSION,
    createdAt: new Date(now).toISOString(),
    updatedAt: new Date(now).toISOString(),
    xp: 0,
    counters: {
      events: 0,
      turns: 0,
      okTurns: 0,
      errorTurns: 0,
      toolCalls: 0,
      toolErrors: 0,
      approvals: 0,
      recoveries: 0,
    },
    tools: {},
    projects: {},
    hours: emptyHours(),
    days: {},
    activeMs: 0,
    errorStreak: 0,
    errorStreaks: {},
    unattributedErrorStreak: 0,
    bestErrorStreak: 0,
    learned: [],
    diary: [],
    lastReviewAt: null,
  }
}

function asProfile(raw, now) {
  const base = emptyProfile(now)
  if (!raw || typeof raw !== 'object') return base
  const profile = {
    ...base,
    ...raw,
    counters: { ...base.counters, ...(raw.counters && typeof raw.counters === 'object' ? raw.counters : {}) },
    tools: raw.tools && typeof raw.tools === 'object' ? { ...raw.tools } : {},
    projects: raw.projects && typeof raw.projects === 'object' ? { ...raw.projects } : {},
    days: raw.days && typeof raw.days === 'object' ? { ...raw.days } : {},
    hours: Array.isArray(raw.hours) && raw.hours.length === 24 ? raw.hours.map((n) => Number(n) || 0) : emptyHours(),
    learned: Array.isArray(raw.learned) ? raw.learned.filter((row) => row && row.id && row.text) : [],
    diary: Array.isArray(raw.diary) ? raw.diary.filter((row) => row && typeof row.text === 'string') : [],
  }
  const count = (value) => Number.isFinite(value) && value >= 0 ? value : 0
  profile.xp = count(profile.xp)
  profile.activeMs = count(profile.activeMs)
  profile.errorStreak = count(profile.errorStreak)
  profile.errorStreaks = raw.errorStreaks && typeof raw.errorStreaks === 'object' && !Array.isArray(raw.errorStreaks) ? { ...raw.errorStreaks } : {}
  for (const key of Object.keys(profile.errorStreaks)) profile.errorStreaks[key] = count(profile.errorStreaks[key])
  // Old profiles know a streak but not which session caused it. Keep that
  // historical count, without awarding a recovery to an unrelated session.
  profile.unattributedErrorStreak = raw.errorStreaks ? count(raw.unattributedErrorStreak) : profile.errorStreak
  profile.errorStreak = profile.unattributedErrorStreak + Object.values(profile.errorStreaks).reduce((sum, value) => sum + value, 0)
  profile.bestErrorStreak = Math.max(count(profile.bestErrorStreak), profile.errorStreak)
  for (const key of Object.keys(base.counters)) profile.counters[key] = count(profile.counters[key])
  for (const table of [profile.tools, profile.projects, profile.days]) {
    for (const key of Object.keys(table)) table[key] = count(table[key])
  }
  profile.hours = profile.hours.map(count)
  profile.recentEvents = Array.isArray(raw.recentEvents) ? raw.recentEvents.filter((key) => typeof key === 'string').slice(-2048) : []
  profile.queueRecords = Array.isArray(raw.queueRecords) ? raw.queueRecords.filter((key) => typeof key === 'string').slice(-2048) : []
  profile.version = PROFILE_VERSION
  return profile
}

function bump(store, key, amount = 1) {
  if (!key) return
  const name = String(key).slice(0, 60)
  const previous = Object.hasOwn(store, name) && Number.isFinite(store[name]) ? store[name] : 0
  Object.defineProperty(store, name, { value: previous + amount, writable: true, enumerable: true, configurable: true })
}

function projectNameOf(session, event) {
  const direct = event?.data?.projectName ?? event?.data?.project ?? session?.projectName ?? session?.project
  if (typeof direct === 'string' && direct.trim()) return direct.trim().slice(0, 60)
  const cwd = event?.data?.cwd ?? session?.cwd
  if (typeof cwd === 'string' && cwd.trim()) {
    const parts = cwd.replace(/[\\/]+$/, '').split(/[\\/]/)
    const tail = parts[parts.length - 1]
    if (tail) return tail.slice(0, 60)
  }
  return undefined
}

function sessionKey(session) {
  return String(session?.id ?? session?.sessionId ?? session?.key ?? 'session')
}

/**
 * Apply one DSH session event to the profile in place.
 * Returns a summary of what changed so the caller can log or announce it.
 */
const OBSERVED_TYPES = new Set(['tool/call', 'tool/result', 'turn/start', 'turn/end', 'approval/decided'])

export function observe(profile, { event, session, now = Date.now(), turnStart } = {}) {
  const unchanged = { gained: 0, leveledUp: false, learned: [] }
  if (!event || !OBSERVED_TYPES.has(event.type)) return unchanged
  // Do not hash payloads: identical legitimate calls are common. Deduplicate
  // only when the producer supplies a stable event/operation identity.
  const identity = event.seq ?? event.sequence ?? event.eventId ?? event.id ??
    (event.type.startsWith('tool/') ? event.data?.toolCallId ?? event.data?.callId :
      event.type.startsWith('turn/') ? event.data?.turnId : event.data?.approvalId)
  if (identity != null) {
    const key = JSON.stringify([sessionKey(session), event.type, identity])
    const recent = profile.recentEvents ??= []
    if (recent.includes(key)) return unchanged
    recent.push(key)
    if (recent.length > 2048) recent.shift()
  }
  const date = new Date(now)
  const profile0Level = levelFromXp(profile.xp)
  let gained = 0

  const C = profile.counters
  C.events += 1
  profile.hours[date.getHours()] = (profile.hours[date.getHours()] ?? 0) + 1
  const dayKey = localDateKey(date)
  const firstEventToday = !profile.days[dayKey]
  profile.days[dayKey] = (profile.days[dayKey] ?? 0) + 1
  if (firstEventToday) gained += 20

  const project = projectNameOf(session, event)
  if (project) bump(profile.projects, project)

  switch (event.type) {
    case 'tool/call': {
      const name = String(event.data?.name ?? 'tool')
      C.toolCalls += 1
      bump(profile.tools, name)
      gained += 2
      break
    }
    case 'tool/result': {
      const key = sessionKey(session)
      const streaks = profile.errorStreaks ??= {}
      const streak = Object.hasOwn(streaks, key) ? streaks[key] : 0
      if (event.data?.error) {
        C.toolErrors += 1
        Object.defineProperty(streaks, key, { value: streak + 1, writable: true, enumerable: true, configurable: true })
        profile.errorStreak += 1
        profile.bestErrorStreak = Math.max(profile.bestErrorStreak, profile.errorStreak)
        gained += 3
      } else if (streak > 0) {
        C.recoveries += 1
        delete streaks[key]
        profile.errorStreak = Math.max(0, profile.errorStreak - streak)
        gained += 8
      }
      break
    }
    case 'turn/start': {
      break
    }
    case 'turn/end': {
      const kind = String(event.data?.reason?.kind ?? 'completed')
      C.turns += 1
      const duration = Number.isFinite(turnStart) ? Math.max(0, now - turnStart) : 0
      profile.activeMs += Math.min(duration, 2 * 60 * 60 * 1000)
      if (kind === 'completed') {
        C.okTurns += 1
        gained += 10
      } else if (kind === 'error') {
        C.errorTurns += 1
        gained += 3
      }
      break
    }
    case 'approval/decided': {
      C.approvals += 1
      gained += 5
      break
    }
    default:
      break
  }

  profile.xp += gained
  profile.updatedAt = date.toISOString()
  const leveledUp = levelFromXp(profile.xp) > profile0Level
  return { gained, leveledUp, learned: recomputeLearned(profile, now), accepted: true }
}

function dayStreak(profile, now) {
  const date = new Date(now)
  date.setHours(12, 0, 0, 0)
  let streak = 0
  for (let i = 0; i < 400; i += 1) {
    const key = localDateKey(date)
    date.setDate(date.getDate() - 1) // calendar days, not 24h across DST changes
    if (profile.days[key]) streak += 1
    else if (i === 0) continue // today may not have activity yet
    else break
  }
  return streak
}

function topEntry(store) {
  let best
  for (const [key, count] of Object.entries(store)) {
    if (!best || count > best.count) best = { key, count }
  }
  return best
}

/**
 * Derive the "learned" statements from the counters. Statements are stable
 * ids with fresh text, so the UI can diff and the profile stays small.
 */
export function recomputeLearned(profile, now = Date.now()) {
  const previous = new Map((profile.learned ?? []).map((row) => [row.id, row]))
  const next = new Map()
  const stamp = new Date(now).toISOString()
  const put = (id, text) => {
    const old = previous.get(id)
    next.set(id, old?.text === text ? old : { id, text, at: stamp })
  }

  const C = profile.counters
  const streak = dayStreak(profile, now)
  if (streak >= 2) put('streak', `已经连续 ${streak} 天陪你工作`)

  const totalEvents = profile.hours.reduce((sum, n) => sum + n, 0)
  if (totalEvents >= 40) {
    const peak = profile.hours.indexOf(Math.max(...profile.hours))
    put('hour', `你最常在 ${String(peak).padStart(2, '0')} 点活跃`)
    const night = profile.hours.slice(22).reduce((a, b) => a + b, 0) + profile.hours.slice(0, 4).reduce((a, b) => a + b, 0)
    if (night / totalEvents >= 0.35) put('night', '你是夜行动物，深夜还在干活')
  }

  const tool = topEntry(profile.tools)
  if (tool && tool.count >= 10) put('tool', `最常用的工具是 ${tool.key}（${tool.count} 次）`)

  const project = topEntry(profile.projects)
  if (project && project.count >= 20) put('project', `在 ${project.key} 这个项目上干得最多`)

  if (C.toolErrors >= 5) {
    put('errors', `遇到过 ${C.toolErrors} 次工具报错，自己爬起来了 ${C.recoveries} 次`)
  }

  const rows = [...next.values()].sort((a, b) => String(b.at).localeCompare(String(a.at)))
  profile.learned = rows.slice(0, 12)
  return profile.learned.map((row) => row.id)
}

export function traitsOf(profile) {
  const C = profile.counters
  const minutes = profile.activeMs / 60000
  const distinctTools = Object.keys(profile.tools).length
  const distinctProjects = Object.keys(profile.projects).length
  const attempts = C.okTurns + C.errorTurns
  return {
    stamina: Math.min(100, Math.round(minutes / 2)),
    wit: Math.min(100, distinctTools * 6 + distinctProjects * 6),
    affinity: Math.min(100, Math.round(C.turns * 0.8 + C.approvals * 4)),
    calm: attempts === 0 ? 50 : Math.min(100, Math.round((100 * C.okTurns) / attempts)),
  }
}

export function addDiary(profile, text, now = Date.now(), extra = {}) {
  const clean = String(text ?? '').trim().slice(0, 800)
  if (!clean) return false
  profile.diary = [
    { at: new Date(now).toISOString(), text: clean, level: levelFromXp(profile.xp), ...extra },
    ...(profile.diary ?? []),
  ].slice(0, 20)
  profile.lastReviewAt = new Date(now).toISOString()
  profile.updatedAt = profile.lastReviewAt
  return true
}

export function resetProfile(now = Date.now()) {
  return emptyProfile(now)
}

export function loadProfile(raw, now = Date.now()) {
  return asProfile(raw, now)
}

/** Public view consumed by the client and the CLI. */
export function derive(profile, now = Date.now()) {
  const progress = levelProgress(profile.xp)
  const stage = stageOf(progress.level)
  const streak = dayStreak(profile, now)
  const current = { ...profile }
  recomputeLearned(current, now)
  return {
    version: PROFILE_VERSION,
    xp: profile.xp,
    level: progress.level,
    into: progress.into,
    need: progress.need,
    ratio: progress.ratio,
    stage: { key: stage.key, name: stage.name, title: stage.title, accent: stage.accent },
    streak,
    traits: traitsOf(profile),
    counters: { ...profile.counters },
    topTools: Object.entries(profile.tools).sort((a, b) => b[1] - a[1]).slice(0, 6),
    topProjects: Object.entries(profile.projects).sort((a, b) => b[1] - a[1]).slice(0, 6),
    learned: current.learned,
    diary: profile.diary ?? [],
    lastReviewAt: profile.lastReviewAt,
    createdAt: profile.createdAt,
    updatedAt: profile.updatedAt,
  }
}
