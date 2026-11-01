// Debounced atomic JSON persistence. Unreadable data is never replaced by a blank profile.
import { mkdirSync, readFileSync, readdirSync, renameSync, writeFileSync, unlinkSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
import { homedir } from 'node:os'
import { basename, dirname, join, resolve } from 'node:path'

export function defaultProfilePath({ env = process.env, home = homedir() } = {}) {
  const override = env.DSH_DAFEIYU_GROWTH_FILE
  if (override) return resolve(override)
  return join(env.DSH_HOME || join(home, '.dsh'), 'dsh-dafeiyu-growth', 'profile.json')
}

export function queuePathForProfile(file) {
  const name = basename(file)
  return join(dirname(file), name.toLowerCase() === 'profile.json' ? 'pending-diary.jsonl' : `${name}.pending-diary.jsonl`)
}

export function createStore(file, {
  readFile = readFileSync, writeFile = writeFileSync, moveFile = renameSync,
  removeFile = unlinkSync, makeDirectory = mkdirSync, listDirectory = readdirSync, debounceMs = 1500, warn = () => {},
} = {}) {
  let blocked = false
  let readError = null
  let writeError = null
  let dirty = false
  let disposed = false
  let timer
  const read = () => {
    if (!file) return undefined
    try {
      const value = JSON.parse(readFile(file, 'utf8'))
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('profile must be an object')
      blocked = false
      readError = null
      return value
    } catch (error) {
      blocked = error?.code !== 'ENOENT'
      readError = blocked ? error.message : null
      if (blocked) warn(`unable to read growth profile; preserving original: ${error.message}`)
      return undefined
    }
  }
  let state = read()
  const writeNow = () => {
    if (disposed || blocked || !file) return false
    if (!dirty) return true
    const temporary = `${file}.${process.pid}.${randomUUID()}.tmp`
    try {
      makeDirectory(dirname(file), { recursive: true })
      writeFile(temporary, `${JSON.stringify(state, null, 2)}\n`, 'utf8')
      moveFile(temporary, file)
      dirty = false
      writeError = null
      return true
    } catch (error) {
      writeError = error.message
      warn(`unable to save growth profile: ${error.message}`)
      try { removeFile(temporary) } catch {}
      return false
    }
  }
  const clearTimer = () => {
    if (timer) clearTimeout(timer)
    timer = undefined
  }
  return {
    path: file,
    health() {
      return { ok: !blocked && !writeError, readable: !blocked, writable: !blocked && !writeError, error: readError ?? writeError }
    },
    read,
    load() { state = read(); dirty = false; return state },
    get() { return state },
    set(next) { if (!disposed) { state = next; dirty = true } },
    save({ immediate = false } = {}) {
      if (disposed || blocked) return false
      if (immediate) { clearTimer(); return writeNow() }
      if (!timer && dirty) {
        timer = setTimeout(() => { timer = undefined; writeNow() }, debounceMs)
        timer.unref?.()
      }
      return true
    },
    backup() {
      if (disposed || blocked || !file) return false
      const backup = `${file}.${Date.now()}.${randomUUID()}.bak`
      try {
        makeDirectory(dirname(file), { recursive: true })
        writeFile(backup, `${JSON.stringify(state, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' })
        // Reset keeps every write under .bak; bound them so "清零" cannot leave
        // an unbounded history of complete personal profiles on disk.
        try {
          const folder = dirname(file)
          const prefix = `${basename(file)}.`
          const stale = listDirectory(folder)
            .filter((name) => name.startsWith(prefix) && name.endsWith('.bak'))
            .sort().reverse().slice(3)
          for (const name of stale) {
            try { removeFile(join(folder, name)) } catch {}
          }
        } catch {}
        return backup
      } catch (error) {
        writeError = error.message
        warn(`unable to back up growth profile: ${error.message}`)
        return false
      }
    },
    flush() { clearTimer(); return writeNow() },
    dispose() { clearTimer(); disposed = true },
  }
}
