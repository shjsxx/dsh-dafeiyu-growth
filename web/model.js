// Presentation-only normalization. No persistence, network, or clock reads at import.
const MAX_COUNT = Number.MAX_SAFE_INTEGER
const record = (value) => value && typeof value === 'object' && !Array.isArray(value) ? value : {}
const number = (value, fallback = 0, max = MAX_COUNT) => typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.min(max, value)) : fallback
const count = (value, fallback = 0, max = MAX_COUNT) => Math.floor(number(value, fallback, max))
const text = (value, max = 800) => typeof value === 'string' ? value.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '').trim().slice(0, max) : ''
export const STAGES = Object.freeze([
  { key: 'egg', name: '云芽', minLevel: 1, title: '刚凝成的一缕云气', accent: '#a9c6da' },
  { key: 'fry', name: '听风', minLevel: 5, title: '会追着风跑的小云灵', accent: '#7fa9c9' },
  { key: 'fish', name: '御风', minLevel: 10, title: '能自己穿过云层', accent: '#5c8fbf' },
  { key: 'big', name: '踏云', minLevel: 20, title: '踏着云海走了很远', accent: '#3f6fa8' },
  { key: 'whale', name: '岚鲸', minLevel: 35, title: '云海尽头的鲸', accent: '#2b4e7e' },
].map(Object.freeze))
export function normalizeTimestamp(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T/.test(value)) return null
  const datePart = value.slice(0, 10)
  const dateStamp = Date.parse(`${datePart}T00:00:00Z`)
  if (!Number.isFinite(dateStamp) || new Date(dateStamp).toISOString().slice(0, 10) !== datePart) return null
  const stamp = Date.parse(value)
  if (!Number.isFinite(stamp)) return null
  return new Date(stamp).toISOString()
}
function rows(value, limit, diary = false) {
  return (Array.isArray(value) ? value : []).slice(0, 100).flatMap((item) => {
    const row = record(item)
    const clean = text(row.text)
    if (!clean) return []
    return [{ ...(diary ? { level: Math.max(1, count(row.level, 1, 60)), kind: text(row.kind, 40) } : { id: text(row.id, 80) }), text: clean, at: normalizeTimestamp(row.at) }]
  }).slice(0, limit)
}
function ranking(value) {
  return (Array.isArray(value) ? value : []).slice(0, 6).flatMap((row) => Array.isArray(row) && text(row[0], 60) ? [[text(row[0], 60), count(row[1])]] : [])
}
export function normalizeGrowth(raw) {
  const source = record(raw)
  const level = Math.max(1, count(source.level, 1, 60))
  const rawStage = record(source.stage)
  // Keys are the stable contract; never display old English stage labels.
  const stage = STAGES.find((item) => item.key === rawStage.key) ?? [...STAGES].reverse().find((item) => level >= item.minLevel)
  const counters = record(source.counters)
  const traits = record(source.traits)
  const health = record(source.storageHealth)
  const healthKnown = typeof health.ok === 'boolean'
  const need = Math.max(1, number(source.need, 1))
  const into = number(source.into, 0, need)
  const ratio = number(source.ratio, into / need, 1)
  return {
    version: count(source.version, 1), xp: count(source.xp), level, into, need, ratio, percent: Math.round(ratio * 100),
    stage: { ...stage }, streak: count(source.streak, 0, 400),
    traits: Object.fromEntries(['stamina', 'wit', 'affinity', 'calm'].map((key) => [key, count(traits[key], key === 'calm' ? 50 : 0, 100)])),
    counters: Object.fromEntries(['events', 'turns', 'okTurns', 'errorTurns', 'toolCalls', 'toolErrors', 'approvals', 'recoveries'].map((key) => [key, count(counters[key])])),
    topTools: ranking(source.topTools), topProjects: ranking(source.topProjects),
    learned: rows(source.learned, 12), diary: rows(source.diary, 20, true),
    createdAt: normalizeTimestamp(source.createdAt), updatedAt: normalizeTimestamp(source.updatedAt), lastReviewAt: normalizeTimestamp(source.lastReviewAt),
    // Backend error strings can contain private absolute paths. Only expose safe copy.
    storageHealth: { ok: healthKnown ? health.ok : null, readable: typeof health.readable === 'boolean' ? health.readable : null, writable: typeof health.writable === 'boolean' ? health.writable : null, error: healthKnown && !health.ok ? '本地档案读写异常，请检查存储权限与可用空间。' : null },
  }
}
function normalizeInsights(raw) {
  const source = record(raw)
  const memory = record(source.memory)
  const web = record(source.web)
  const cloud = record(source.cloud)
  const auto = record(source.autoLearn)
  const relationship = record(source.relationship)
  const emotion = record(source.emotion)
  const sources = record(source.sources)
  const memoryCounts = {}
  for (const key of ['total', 'private', 'preference', 'habit', 'diary', 'public', 'culture']) memoryCounts[key] = count(memory[key])
  return {
    memory: memoryCounts,
    web: {
      total: count(web.total), max: Math.max(1, count(web.max, 2000)), hosts: count(web.hosts),
      recent: (Array.isArray(web.recent) ? web.recent : []).slice(0, 4).flatMap(row => {
        const item = record(row); const title = text(item.title, 100)
        if (!title) return []
        return [{ title, host: text(item.host, 80), at: normalizeTimestamp(item.at) }]
      }),
    },
    cloud: {
      configured: cloud.configured === true, model: text(cloud.model, 80),
      maxTokens: count(cloud.maxTokens, 800), prefer: cloud.prefer === true, proactive: cloud.proactive === true,
    },
    autoLearn: { enabled: auto.enabled === true, interval: count(auto.interval, 15), batch: count(auto.batch, 3) },
    relationship: { topics: count(relationship.topics), episodes: count(relationship.episodes) },
    emotion: { webLearning: count(emotion.webLearning), label: text(emotion.label, 24) || 'neutral' },
    sources: { sites: count(sources.sites) },
  }
}
export function createDashboardModel(raw, { now = Date.now() } = {}) {
  const state = normalizeGrowth(raw)
  const time = now instanceof Date ? now.getTime() : now
  const created = state.createdAt ? Date.parse(state.createdAt) : NaN
  return { ...state, insights: normalizeInsights(raw?.insights), ageDays: Number.isFinite(time) && Number.isFinite(created) ? Math.max(0, Math.floor((time - created) / 86400000)) : 0,
    hasActivity: state.counters.events > 0,
    learningDescription: '根据真实会话的计数与简单分布归纳习惯，不是模型训练，也不会修改 AI 权重。' }
}
// Deliberate allowlist: never export project/session IDs, project names, diary,
// tool labels, arbitrary server extras, or free-form learned statements.
export function exportGrowth(raw) {
  const state = normalizeGrowth(raw)
  return { version: state.version, xp: state.xp, level: state.level, stage: { key: state.stage.key, name: state.stage.name },
    streak: state.streak, counters: state.counters, traits: state.traits,
    createdAt: state.createdAt, updatedAt: state.updatedAt }
}
