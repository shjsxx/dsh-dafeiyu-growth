#!/usr/bin/env node
import { pathToFileURL } from 'node:url'
import { createDshPetPilot, DSH_PET_CONTRACT, PET_DEMO_SCENES } from '../src/pet/dsh-pet-pilot.js'
import { createPetPilotServer } from '../test/helpers/pet-pilot-server.js'

export const HELP = `Isolated dsh-pet synthetic pilot (default: --mock)
Usage: node tools/pet-pilot.mjs [--mock] [--scene greeting|quiet|celebrate]
Live: --endpoint http://127.0.0.1:PORT --confirm-isolated --pet DEDICATED_ID
      --contract ${DSH_PET_CONTRACT.commit} --ack-local-api-risk
Optional trusted animation names: --greet-animation NAME --celebrate-animation NAME
Only fixed synthetic scenes; no user text, profile/key access, discovery, downloads or installation.
Live requires a dedicated test pet configured display=both. This selects eligibility, not output isolation.
The unauthenticated local API remains a risk. The commit assertion does NOT automatically verify upstream version.
Ctrl+C awaits bounded adapter cleanup; it does NOT confirm that remote animation stopped.
`

export function parseArgs(argv) {
  const values = new Set(['--endpoint', '--pet', '--contract', '--scene', '--greet-animation', '--celebrate-animation'])
  const flags = new Set(['--help', '--mock', '--confirm-isolated', '--ack-local-api-risk'])
  const args = Object.create(null)
  for (let i = 0; i < argv.length; i++) {
    const key = argv[i]
    if ((!values.has(key) && !flags.has(key)) || Object.hasOwn(args, key)) throw new Error('Unknown or duplicate argument; use --help.')
    if (flags.has(key)) args[key] = true
    else {
      const value = argv[++i]
      if (!value || value.startsWith('--')) throw new Error('Missing argument value; use --help.')
      args[key] = value
    }
  }
  if (args['--help']) return { help: true }
  const scene = args['--scene'] ?? 'greeting'
  if (!Object.hasOwn(PET_DEMO_SCENES, scene)) throw new Error('Only fixed synthetic scenes are allowed.')
  const live = Object.hasOwn(args, '--endpoint')
  if (live && args['--mock']) throw new Error('Do not combine --mock and --endpoint.')
  if (!live && ['--pet', '--contract', '--confirm-isolated', '--ack-local-api-risk'].some(key => Object.hasOwn(args, key))) throw new Error('Live consent flags require an explicit endpoint.')
  if (live) {
    if (!/^http:\/\/127\.0\.0\.1:[1-9]\d{0,4}$/.test(args['--endpoint']) || Number(args['--endpoint'].split(':').at(-1)) > 65535) throw new Error('Endpoint must be exact numeric IPv4 loopback with explicit port.')
    if (!args['--confirm-isolated'] || !args['--ack-local-api-risk'] || args['--contract'] !== DSH_PET_CONTRACT.commit || !/^[a-zA-Z0-9_-]{1,64}$/.test(args['--pet'] ?? '') || args['--pet'] === 'main') throw new Error('Live mode requires isolation confirmation, dedicated pet, matching contract and local API risk acknowledgement.')
  }
  const animations = live ? {} : { greet: '演示点头', celebrate: '演示庆祝' }
  for (const [flag, intent] of [['--greet-animation', 'greet'], ['--celebrate-animation', 'celebrate']]) {
    if (!args[flag]) continue
    const name = args[flag]
    if (!name.trim() || name.length > 80 || /[\x00-\x1f\x7f/\\:?&#]/.test(name) || name.includes('..')) throw new Error('Invalid trusted animation name.')
    animations[intent] = name
  }
  return { live, scene, baseUrl: args['--endpoint'], petId: live ? args['--pet'] : 'yunlan-pilot', animations }
}

export async function main(argv = process.argv.slice(2)) {
  const args = parseArgs(argv)
  if (args.help) { console.log(HELP); return 0 }
  let mock
  let pilot
  let interrupted = false
  let cancelling
  const interrupt = () => { interrupted = true; cancelling ??= pilot?.cancel() }
  try {
    if (!args.live) mock = await createPetPilotServer({ petId: args.petId })
    pilot = createDshPetPilot({ enabled: true, acknowledgeUnauthenticatedLocalApi: true,
      contractCommit: DSH_PET_CONTRACT.commit, baseUrl: mock?.baseUrl ?? args.baseUrl,
      petId: args.petId, animations: args.animations })
    process.on('SIGINT', interrupt)
    if (!pilot.status().enabled) throw new Error('Adapter rejected pilot configuration.')
    console.log(args.live ? 'Explicit isolated live mode; display=both required. Upstream version is NOT automatically verified.' : 'Isolated ephemeral loopback mock; synthetic scenes only.')
    const report = await pilot.present(args.scene)
    console.log(JSON.stringify(report, null, 2))
    console.log('Cleanup receipts do not confirm remote animation stopped.')
    return interrupted ? 130 : report.ok ? 0 : 1
  } finally {
    await cancelling
    await pilot?.cancel()
    await pilot?.close()
    await mock?.close()
    process.removeListener('SIGINT', interrupt)
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try { process.exitCode = await main() }
  catch { console.error('Pilot failed. Use --help for strict isolated-mode requirements.'); process.exitCode = 1 }
}
