/**
 * The line between running the app and acquiring software, on the host side. It mirrors
 * strata_control/acquisition.py: starting a model, serving, and opening the app never download or install
 * an engine, a connector, a package or a compiler. Anything that would (the llama.cpp fetch, the cloudflared
 * fetch) calls `require()` first, and `require()` refuses where acquiring is forbidden.
 *
 * Forbidden means: STRATA_NO_ACQUIRE is 1/true/yes/on, this is the packaged app (STRATA_PACKAGED=1),
 * or this is the Windows offline entry (STRATA_OFFLINE_ENTRY=1). The offline entry names the missing
 * local file and does not point at an installer. The Tauri shell sets the first two for the host.
 * Models the user chooses to download are a different, explicit action and do not go through here,
 * except on the offline entry, where a download is refused as well.
 */

const TRUE = new Set(['1', 'true', 'yes', 'on']);

export class AcquisitionRefused extends Error {
  constructor(what, command = 'the Terminal command `strata install`') {
    super(`${what} is not installed here, and the app never downloads it while it runs. Install it from ${command}.`);
    this.name = 'AcquisitionRefused';
    this.code = 'acquisition_refused';
    this.status = 409;
    this.what = what;
  }
}

export function offlineEntry(env = process.env) {
  return String(env.STRATA_OFFLINE_ENTRY ?? '').trim() === '1';
}

export function forbidden(env = process.env) {
  return offlineEntry(env) || TRUE.has(String(env.STRATA_NO_ACQUIRE ?? '').trim().toLowerCase()) || env.STRATA_PACKAGED === '1';
}

/** Call before any download or install. Throws AcquisitionRefused where acquiring is forbidden. */
export function require(what, command, env = process.env) {
  if (offlineEntry(env)) {
    const error = new AcquisitionRefused(what, command);
    error.message = `${what} is missing from the offline folder. Nothing was downloaded and no installer was started.`;
    throw error;
  }
  if (forbidden(env)) throw new AcquisitionRefused(what, command);
}
