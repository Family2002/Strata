/**
 * The Windows offline entry (`strata offline`, STRATA_OFFLINE_ENTRY=1).
 *
 * These answers are local. They do not call the update feed, and they do not tell the person to
 * download or install a runtime. A missing engine names the folder it should already be in.
 */
import { existsSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

export function isOfflineEntry(env = process.env) {
  return String(env.STRATA_OFFLINE_ENTRY ?? '').trim() === '1';
}

export function enginePackagePresent(components, exists = existsSync, list = readdirSync) {
  const engine = join(components, 'engine');
  try {
    return list(engine).some(name => exists(join(engine, name, 'engine-component.json')));
  } catch {
    return false;
  }
}

export function localFirstRun(components, exists = existsSync, list = readdirSync) {
  const engine = join(components, 'engine');
  if (enginePackagePresent(components, exists, list)) {
    return { status: 'installed', gpus: [], message: 'The engine in the offline folder is used. Nothing was downloaded.' };
  }
  return {
    status: 'missing-local',
    gpus: [],
    message: `Missing local file: ${join(engine, '<package>', 'engine-component.json')}. Nothing was downloaded.`,
  };
}

export function localUpdateReport(now = new Date().toISOString()) {
  const message = 'The offline entry does not check for updates. Nothing was contacted.';
  return {
    checkedAt: now,
    feed: null,
    strata: { status: 'local', message },
    engine: { status: 'local', message: 'The engine in the offline folder is used as it is. Nothing was downloaded.' },
  };
}

export function offlineNetworkRefusal(kind) {
  return `The offline entry does not download or install ${kind}. Nothing was contacted.`;
}
