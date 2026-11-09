// Read-only, privacy-bounded dashboard context from local companion files.
// Counts and public source titles only; no chat text, keys, or private paths.
import { existsSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

const MAX_FILE = 4 * 1024 * 1024
const safeText = (value, max = 80) => typeof value === 'string'
  ? value.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '').trim().slice(0, max)
  : ''
const safeCount = (value, max = 1e9) => typeof value === 'number' && Number.isFinite(value)
  ? Math.max(0, Math.min(max, Math.floor(value)))
  : 0

function readJson(dataDir, name) {
  const path = join(dataDir, name)
  try {
    if (!existsSync(path) || !statSync(path).isFile() || statSync(path).size > MAX_FILE) return null
    const parsed = JSON.parse(readFileSync(path, 'utf8'))
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null
  } catch {
    return null
  }
}

function countRows(rows) {
  const result = { total: 0, private: 0, preference: 0, habit: 0, diary: 0, public: 0, culture: 0 }
  for (const row of Array.isArray(rows) ? rows : []) {
    if (!row || typeof row !== 'object') continue
    result.total += 1
    const kind = safeText(row.kind, 20)
    if (kind in result) result[kind] += 1
    if (kind === 'preference' || kind === 'habit' || kind === 'diary') result.private += 1
  }
  return result
}

export function collectInsights(dataDir) {
  const memoryRaw = readJson(dataDir, 'memory.json')
  const memory = countRows(memoryRaw?.records)
  const webRaw = readJson(dataDir, 'web-knowledge.json')
  const webRecords = Array.isArray(webRaw?.records) ? webRaw.records : []
  const hosts = new Set()
  const recentWeb = []
  for (const row of webRecords) {
    if (!row || typeof row !== 'object') continue
    const host = safeText(row.host, 80)
    if (host) hosts.add(host)
    if (recentWeb.length < 4) {
      recentWeb.push({ title: safeText(row.title, 100) || '未命名公开资料', host,
        at: typeof row.fetchedAt === 'number' && Number.isFinite(row.fetchedAt)
          ? new Date(row.fetchedAt * 1000).toISOString() : null })
    }
  }
  const cloudRaw = readJson(dataDir, 'cloud.json')
  const autoRaw = readJson(dataDir, 'auto-learning.json')
  const relationshipRaw = readJson(dataDir, 'relationship.json')
  const emotionRaw = readJson(dataDir, 'emotion.json')
  const sourcesRaw = readJson(dataDir, 'web-sources.json')
  const sites = Array.isArray(sourcesRaw?.sites) ? sourcesRaw.sites : []
  const webLearning = Array.isArray(emotionRaw?.webLearning) ? emotionRaw.webLearning : []
  return {
    memory,
    web: {
      total: webRecords.length,
      max: safeCount(webRaw?.maxRecords, 10000) || 2000,
      hosts: hosts.size,
      recent: recentWeb,
    },
    cloud: {
      configured: Boolean(cloudRaw?.key_protected),
      model: safeText(cloudRaw?.model, 80),
      maxTokens: safeCount(cloudRaw?.max_tokens, 4096),
      prefer: cloudRaw?.prefer_cloud === true,
      proactive: cloudRaw?.proactive_smart === true,
    },
    autoLearn: {
      enabled: autoRaw?.enabled === true,
      interval: safeCount(autoRaw?.interval, 1440),
      batch: safeCount(autoRaw?.batch, 20),
      lastAt: typeof autoRaw?.lastAt === 'number' && Number.isFinite(autoRaw.lastAt)
        ? new Date(autoRaw.lastAt * 1000).toISOString() : null,
    },
    relationship: {
      topics: Array.isArray(relationshipRaw?.topics) ? relationshipRaw.topics.length : 0,
      episodes: Array.isArray(relationshipRaw?.episodes) ? relationshipRaw.episodes.length : 0,
    },
    emotion: {
      webLearning: webLearning.length,
      label: safeText(emotionRaw?.current?.label || emotionRaw?.mood || 'neutral', 24),
    },
    sources: { sites: sites.length },
  }
}
