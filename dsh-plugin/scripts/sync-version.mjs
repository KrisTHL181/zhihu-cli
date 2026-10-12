#!/usr/bin/env node
/**
 * Keep this bundle's `version` in step with the installed zhihu-toolkit.
 *
 * The toolkit's version comes from setuptools-scm, so it is a PEP 440 string
 * (`0.2.1.dev14`) while an npm manifest needs SemVer (`0.2.1-dev.14`). The
 * mapping is mechanical and lives in {@link toSemver}.
 *
 * Two modes:
 *
 *   default    Write the detected version into the manifest. Never fails: when
 *              no toolkit can be found the manifest is left exactly as
 *              committed and a note is printed. Used by the release pipeline,
 *              and by a developer who wants local parity.
 *   --check    Write nothing; refuse when the manifest still carries the
 *              committed sentinel. This is the `prepublishOnly` guard, so the
 *              file is never rewritten by an install and the working tree
 *              stays clean.
 *
 *   node scripts/sync-version.mjs                    # sync in place
 *   node scripts/sync-version.mjs --check            # guard only
 *   ZHIHU_CLI_VERSION=0.3.0 node scripts/sync-version.mjs
 *   ZHIHU_PYTHON=/path/to/python node scripts/sync-version.mjs
 */

import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const manifestPath = join(dirname(dirname(fileURLToPath(import.meta.url))), 'package.json')

/** PEP 440 release + optional pre/post segment, tolerant of a leading `v`. */
const PEP440 = /^v?(\d+(?:\.\d+)*)(?:[._-]?(a|b|c|rc|alpha|beta|pre|preview|dev|post|rev|r)[._-]?(\d+)?)?(?:\+.*)?$/i

/** PEP 440 spellings mapped onto the SemVer pre-release identifiers we use. */
const LABELS = {
  a: 'alpha', alpha: 'alpha',
  b: 'beta', beta: 'beta',
  c: 'rc', rc: 'rc', pre: 'rc', preview: 'rc',
  dev: 'dev',
  post: 'post', rev: 'post', r: 'post',
}

/**
 * Map a PEP 440 version onto SemVer.
 *
 * `0.2.1.dev14` -> `0.2.1-dev.14`, `0.2.1rc1` -> `0.2.1-rc.1`,
 * `0.2.1` -> `0.2.1`. A local version segment (`+g1e57c15`) is dropped, and a
 * short release is padded, because SemVer requires three components.
 *
 * @param {string} version - the PEP 440 version string.
 * @returns {string | undefined} the SemVer equivalent, or undefined if unmappable.
 */
export function toSemver(version) {
  const match = PEP440.exec(String(version).trim())
  if (!match) return undefined
  const [, release, label, number] = match
  const parts = release.split('.').map(Number)
  while (parts.length < 3) parts.push(0)
  const core = parts.slice(0, 3).join('.')
  if (!label) return core
  const name = LABELS[label.toLowerCase()]
  return number === undefined ? `${core}-${name}` : `${core}-${name}.${number}`
}

/**
 * Run a command, returning trimmed stdout or undefined on any failure.
 *
 * @param {string} command - executable to run.
 * @param {string[]} args - its arguments.
 * @returns {string | undefined} trimmed stdout.
 */
function run(command, args) {
  try {
    return execFileSync(command, args, {
      encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 20_000,
    }).trim() || undefined
  } catch {
    return undefined
  }
}

/**
 * Find the toolkit version, preferring the very executable the plugin runs.
 *
 * @returns {{ raw: string, source: string } | undefined} the version and where it came from.
 */
function detect() {
  if (process.env.ZHIHU_CLI_VERSION) {
    return { raw: process.env.ZHIHU_CLI_VERSION, source: 'ZHIHU_CLI_VERSION' }
  }
  const banner = run('zhihu', ['--version'])
  if (banner) {
    const token = /(\d[^\s]*)\s*$/.exec(banner)
    if (token) return { raw: token[1], source: 'zhihu --version' }
  }
  const python = process.env.ZHIHU_PYTHON || 'python3'
  const metadata = run(python, ['-c', "import importlib.metadata as m; print(m.version('zhihu-toolkit'))"])
  if (metadata) return { raw: metadata, source: `${python} -c importlib.metadata` }
  return undefined
}

/** The version committed to the repository; the release pipeline replaces it. */
export const SENTINEL = '0.0.0'

const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
const found = detect()

if (process.argv.includes('--check')) {
  if (manifest.version === SENTINEL) {
    console.error(`[sync-version] refusing to publish the committed sentinel ${SENTINEL}`)
    console.error('[sync-version] let the release pipeline fill it in from the tag first, e.g.')
    console.error('[sync-version]   ZHIHU_CLI_VERSION="${GITHUB_REF_NAME#v}" node scripts/sync-version.mjs')
    process.exit(1)
  }
  console.log(`[sync-version] ${manifest.version} is release-ready`)
  process.exit(0)
}

if (!found) {
  console.log(`[sync-version] no zhihu-toolkit found; keeping version ${manifest.version}`)
} else {
  const next = toSemver(found.raw)
  if (!next) {
    console.log(`[sync-version] cannot map "${found.raw}" to SemVer; keeping version ${manifest.version}`)
  } else if (manifest.version === next) {
    console.log(`[sync-version] already ${next} (${found.source}: ${found.raw})`)
  } else {
    manifest.version = next
    writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`)
    console.log(`[sync-version] ${next} (${found.source}: ${found.raw})`)
  }
}
