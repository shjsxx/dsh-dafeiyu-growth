// Self-contained native supervisor for the DSH-only distribution.
//
// It starts the bundled helper with the external renderer marker, keeps it
// alive, and stops it with the DSH plugin lifecycle. It is deliberately inert
// when no bundled runtime exists, so the development/plugin install that still
// uses dsh-dafeiyu keeps working unchanged.
import { spawn as spawnProcess } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const FALLBACK_NOTE = 'bundled runtime not found; native helper left to dsh-dafeiyu'

export function pluginRootFrom(url) {
  return path.dirname(path.dirname(fileURLToPath(url)))
}

export function resolveBundle(root, env = process.env) {
  const base = env.DSH_YUNLAN_BUNDLE_ROOT || path.join(root, 'runtime')
  const win = process.platform === 'win32'
  const electron = env.DSH_YUNLAN_ELECTRON || path.join(base, 'electron', win ? 'DeepSeek Harness.exe' : 'electron')
  const helper = env.DSH_YUNLAN_HELPER || path.join(base, 'helper', win ? 'dsh-dafeiyu-helper.exe' : 'dsh-dafeiyu-helper')
  const prepared = env.DSH_YUNLAN_PREPARED || path.join(base, 'prepared-3c99b1b')
  const models = env.DSH_YUNLAN_MODEL_DIR || path.join(base, 'models', 'bge-small-zh-v1.5')
  const host = env.DSH_YUNLAN_HOST || path.join(root, 'desktop', 'main.cjs')
  const rendererDir = env.DSH_YUNLAN_RENDERER_DIR || path.join(os.homedir(), '.dsh', 'yunlan-renderer')
  return {
    base, electron, helper, prepared, models, host, rendererDir,
    marker: path.join(rendererDir, 'active.json'),
    userData: path.join(rendererDir, 'user-data'),
    lock: path.join(rendererDir, 'supervisor.lock'),
  }
}

export function bundleStatus(paths) {
  const file = (value) => {
    try { return fs.statSync(value).isFile() } catch { return false }
  }
  const dir = (value) => {
    try { return fs.statSync(value).isDirectory() } catch { return false }
  }
  return {
    electron: file(paths.electron),
    helper: file(paths.helper),
    prepared: dir(paths.prepared),
    host: file(paths.host),
    complete: file(paths.electron) && file(paths.helper) && dir(paths.prepared) && file(paths.host),
  }
}

function readLock(file) {
  try {
    const value = JSON.parse(fs.readFileSync(file, 'utf8'))
    if (!Number.isInteger(value?.pid) || value.pid <= 0) return null
    return { pid: value.pid, token: typeof value.token === 'string' ? value.token : '' }
  } catch { return null }
}

function alive(pid) {
  if (!pid) return false
  try { process.kill(pid, 0); return true } catch { return false }
}

export function acquireLock(file, pid = process.pid, token = randomUUID()) {
  const existing = readLock(file)
  if (existing && alive(existing.pid)) return false
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true })
    if (existing) fs.rmSync(file, { force: true })
    fs.writeFileSync(file, JSON.stringify({ pid, token, started: Date.now() }),
      { encoding: 'utf8', flag: 'wx' })
    return true
  } catch (error) {
    if (error?.code === 'EEXIST') return false
    return false
  }
}

export function releaseLock(file, token) {
  const existing = readLock(file)
  if (existing && existing.token === token) fs.rmSync(file, { force: true })
}

export function rendererConfig(paths) {
  return {
    enabled: true,
    electronPath: paths.electron,
    scriptPath: paths.host,
    runtimeRoot: paths.prepared,
    userDataDir: paths.userData,
  }
}

function writeMarker(paths) {
  fs.mkdirSync(paths.rendererDir, { recursive: true })
  fs.mkdirSync(paths.userData, { recursive: true })
  const temporary = `${paths.marker}.${process.pid}.tmp`
  fs.writeFileSync(temporary, JSON.stringify(rendererConfig(paths), null, 2), 'utf8')
  fs.renameSync(temporary, paths.marker)
}

export class NativeSupervisor {
  constructor({ root, config = {}, logger = console, spawnImpl = spawnProcess, env = process.env } = {}) {
    this.root = root
    this.config = config && typeof config === 'object' ? config : {}
    this.logger = logger
    this.spawnImpl = spawnImpl
    this.env = env
    this.paths = resolveBundle(root, env)
    this.child = null
    this.stopping = false
    this.started = false
    this.restarts = 0
    this.timer = null
    this.token = randomUUID()
  }

  status() {
    return { ...bundleStatus(this.paths), running: Boolean(this.child), pid: this.child?.pid ?? null }
  }

  log(level, message) {
    const fn = this.logger?.[level] ?? this.logger?.info
    try { fn?.(`[dsh-dafeiyu-growth] ${message}`) } catch {}
  }

  start() {
    if (this.stopping) return false
    if (this.config.nativeBundle === false) return false
    const status = this.status()
    if (!status.complete) {
      if (this.config.nativeBundle === true) this.log('warn', FALLBACK_NOTE)
      return false
    }
    if (this.started) return true
    if (!acquireLock(this.paths.lock, process.pid, this.token)) {
      this.log('info', 'native supervisor already active in another process')
      return false
    }
    this.started = true
    try {
      writeMarker(this.paths)
    } catch (error) {
      this.log('warn', `cannot write renderer marker: ${error?.message ?? error}`)
      this.started = false
      releaseLock(this.paths.lock, this.token)
      return false
    }
    this.spawnHelper()
    return true
  }

  spawnHelper() {
    if (this.stopping) return
    let child
    const env = { ...this.env }
    // Keep the optional local BGE model inside the plugin bundle instead of
    // reaching into the old dsh-dafeiyu model directory.
    try {
      if (fs.statSync(this.paths.models).isDirectory()) env.DSH_DAFEIYU_EMBEDDING_DIR = this.paths.models
    } catch {}
    try {
      child = this.spawnImpl(this.paths.helper, ['--external-renderer', '--renderer-config', this.paths.marker], {
        cwd: path.dirname(this.paths.helper),
        env,
        windowsHide: true,
        detached: false,
        stdio: ['pipe', 'ignore', 'pipe'],
      })
    } catch (error) {
      this.log('warn', `helper spawn failed: ${error?.message ?? error}`)
      this.scheduleRestart()
      return
    }
    this.child = child
    child.stderr?.on('data', (chunk) => {
      const text = String(chunk).trim()
      if (text) this.log('warn', `helper: ${text.slice(-500)}`)
    })
    child.on?.('error', (error) => this.log('warn', `helper error: ${error?.message ?? error}`))
    child.on?.('exit', (code, signal) => {
      this.child = null
      if (this.stopping) return
      if (code === 0 && !signal) {
        this.log('info', 'native helper exited normally; supervisor stays idle')
        return
      }
      this.log('warn', `helper exited code=${code} signal=${signal}; restarting`)
      this.scheduleRestart()
    })
    this.log('info', `native helper started pid=${child.pid ?? '?'}`)
  }

  scheduleRestart() {
    if (this.stopping || this.timer) return
    if (this.restarts >= 5) {
      this.log('error', 'native helper failed five times; supervisor gave up')
      return
    }
    const delay = Math.min(30000, 2000 * 2 ** this.restarts)
    this.restarts += 1
    this.timer = setTimeout(() => {
      this.timer = null
      if (!this.stopping) this.spawnHelper()
    }, delay)
    this.timer.unref?.()
  }

  stop() {
    this.stopping = true
    if (this.timer) {
      clearTimeout(this.timer)
      this.timer = null
    }
    if (this.child) {
      try { this.child.kill() } catch {}
      this.child = null
    }
    if (this.started) {
      try { fs.rmSync(this.paths.marker, { force: true }) } catch {}
      releaseLock(this.paths.lock, this.token)
    }
    this.started = false
  }
}
