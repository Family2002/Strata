/**
 * The offline entry's host, over HTTP, with the Control process replaced by a script that records
 * the environment it was actually started with and the install arguments it was actually sent.
 * This does not start an engine and does not open a non-loopback socket.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { chmodSync, mkdirSync, writeFileSync, readFileSync, existsSync } from 'node:fs';
import { mkdtemp, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { delimiter, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const preload = pathToFileURL(fileURLToPath(new URL('./helpers/offline-preload.mjs', import.meta.url))).href;
const delay = ms => new Promise(r => setTimeout(r, ms));

async function freePort() {
  const s = createServer();
  await new Promise(r => s.listen(0, '127.0.0.1', r));
  const p = s.address().port;
  await new Promise(r => s.close(r));
  return p;
}

function controlScript() {
  return `#!/usr/bin/env python3
import json, os, sys
record = os.environ["STRATA_CHILD_RECORD"]

def dump(obj):
    with open(record, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(obj, ensure_ascii=False) + "\\n")

args = sys.argv[1:]
if any(str(item).endswith("strata_mcp.py") for item in args):
    dump({"kind": "mcp", "env": {key: os.environ.get(key) for key in ("STRATA_OFFLINE_ENTRY", "STRATA_OFFLINE_MODEL", "STRATA_NO_ACQUIRE", "PATH")}})
    while True:
        raw = sys.stdin.buffer.readline()
        if not raw:
            break
        line = raw.decode("utf-8").strip()
        if not line:
            continue
        msg = json.loads(line)
        method, mid = msg.get("method"), msg.get("id")
        if method == "initialize":
            print(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": "2024-11-05", "capabilities": {}, "serverInfo": {"name": "fake", "version": "0"}}}), flush=True)
        elif method == "tools/list":
            print(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"tools": [{"name": name} for name in ("strata_status", "strata_models", "strata_install")]}}), flush=True)
        elif method == "tools/call":
            params = msg.get("params") or {}
            name, arguments = params.get("name"), params.get("arguments") or {}
            dump({"kind": "call", "name": name, "arguments": arguments})
            if name == "strata_status":
                result = {"summary": "ready", "installed": {"models": []}, "hardware": {"cpu": {"cores": 2}, "ram_gb": 16, "gpus": []}, "install": None}
            elif name == "strata_models":
                result = {"families": [{"family": "qwen", "sizes": [{"family": "qwen", "model": "Q4_K_M", "id": "qwen-q4"}]}], "contexts": []}
            elif name == "strata_install":
                result = {"summary": "recorded", "install": {"running": False, "exit_code": 0, "family": "qwen"}}
            else:
                result = {"summary": name or ""}
            print(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"structuredContent": result}}), flush=True)
    raise SystemExit(0)

if "engine" in args:
    print(json.dumps({"ok": True, "usable": True, "engine": {"version": "0.1.55", "backend": "cuda", "root": "engine"}, "backend": "cuda"}))
elif "list" in args:
    print(json.dumps({"ok": True, "install_dir": os.environ.get("STRATA_INSTALL_DIR"), "models": []}))
elif "drafting" in args:
    print(json.dumps({"ok": True, "serves": False, "needed": False}))
else:
    print(json.dumps({"ok": True}))
`;
}

test('the host passes the offline model folder into the control install and does not call out', { timeout: 30000 }, async t => {
  const home = await mkdtemp(join(tmpdir(), 'strata-offline-model-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const control = join(home, 'control');
  const python = join(control, 'python', 'bin', 'python3');
  mkdirSync(join(control, 'python', 'bin'), { recursive: true });
  mkdirSync(join(control, 'strata_control'), { recursive: true });
  mkdirSync(join(control, 'tools'), { recursive: true });
  mkdirSync(join(control, 'serve'), { recursive: true });
  writeFileSync(python, controlScript());
  chmodSync(python, 0o755);
  writeFileSync(join(control, 'strata.py'), 'print("unused")\n');
  writeFileSync(join(control, 'BUNDLE.json'), '{"version":"0.1.55"}\n');
  writeFileSync(join(control, 'tools', 'strata_mcp.py'), '# reached through the recorded python\n');
  writeFileSync(join(control, 'serve', 'server.py'), '# marker\n');
  const model = join(home, '我的模型');
  mkdirSync(model);
  const chosen = join(home, 'components', 'engine', 'cuda13');
  const other = join(home, 'components', 'engine', 'cuda12');
  mkdirSync(chosen, { recursive: true });
  mkdirSync(other, { recursive: true });
  const record = join(home, 'child.jsonl');
  const log = join(home, 'net.log');
  const port = await freePort();
  const isolated = [chosen, '/usr/bin'].join(delimiter);
  let output = '';
  const host = spawn(process.execPath, ['--import', preload, 'server/index.mjs'], {
    cwd: root,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: {
      ...process.env,
      STRATA_HOME: home,
      STRATA_MODELS_DIR: join(home, 'models'),
      STRATA_PORT: String(port),
      STRATA_BIND: '127.0.0.1',
      STRATA_SHARE_PORT: String(await freePort()),
      STRATA_TUNNEL_PORT: String(await freePort()),
      STRATA_PACKAGED: '1',
      STRATA_NO_BUNDLED_ENGINE: '1',
      STRATA_REAL_ENGINE: '0',
      STRATA_NO_ACQUIRE: '1',
      STRATA_OFFLINE_ENTRY: '1',
      STRATA_OFFLINE_MODEL: model,
      STRATA_CONTROL_DIR: control,
      STRATA_COMPONENTS_DIR: join(home, 'components'),
      STRATA_INSTALL_DIR: home,
      STRATA_SETTINGS_FILE: join(home, 'app-settings.json'),
      STRATA_CHILD_RECORD: record,
      STRATA_NET_LOG: log,
      PATH: isolated,
    },
  });
  host.stdout.on('data', d => { output += d; });
  host.stderr.on('data', d => { output += d; });
  t.after(async () => { host.kill(); await new Promise(r => host.once('exit', r)); });
  let up = false;
  for (let i = 0; i < 150 && !up; i++) {
    try { up = (await fetch(`http://127.0.0.1:${port}/api/health`)).ok; } catch { await delay(100); }
  }
  assert.ok(up, `host started\n${output}`);
  const calls = () => (existsSync(log) ? readFileSync(log, 'utf8').split('\n').filter(Boolean) : []);
  assert.deepEqual(calls(), [], `startup reached out\n${output}`);
  const first = await fetch(`http://127.0.0.1:${port}/api/engine/first-run`);
  assert.equal(first.status, 200);
  assert.match((await first.json()).message, /Nothing was downloaded/);
  const updates = await fetch(`http://127.0.0.1:${port}/api/updates/check`);
  assert.equal(updates.status, 200);
  assert.match((await updates.json()).strata.message, /Nothing was contacted/);
  const blocked = await fetch(`http://127.0.0.1:${port}/api/native/operations`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ kind: 'update-engine', args: {} }),
  });
  const blockedBody = await blocked.json();
  assert.notEqual(blocked.status, 202, JSON.stringify(blockedBody));
  assert.match(blockedBody.error ?? '', /Nothing was contacted/);
  const native = await fetch(`http://127.0.0.1:${port}/api/native?refresh=1`);
  const nativeBody = await native.json();
  assert.equal(native.status, 200, JSON.stringify(nativeBody) + output);
  assert.equal(nativeBody.available, true, (nativeBody.error ?? '') + output);
  const posted = await fetch(`http://127.0.0.1:${port}/api/native/operations`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ kind: 'install', args: { family: 'qwen', model: 'Q4_K_M' } }),
  });
  assert.equal(posted.status, 202, JSON.stringify(await posted.json()) + output);
  let lines = [];
  for (let i = 0; i < 50 && !lines.some(item => item.name === 'strata_install'); i++) {
    await delay(100);
    if (!existsSync(record)) continue;
    lines = readFileSync(record, 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line));
  }
  const started = lines.find(item => item.kind === 'mcp');
  const install = lines.find(item => item.name === 'strata_install');
  assert.ok(started, `control did not start\n${output}\n${readFileSync(record, 'utf8')}`);
  assert.equal(started.env.STRATA_OFFLINE_ENTRY, '1');
  assert.equal(started.env.STRATA_OFFLINE_MODEL, model);
  assert.equal(started.env.STRATA_NO_ACQUIRE, '1');
  assert.equal(started.env.PATH.split(delimiter)[0], chosen);
  assert.equal(started.env.PATH.includes(other), false);
  assert.ok(install, `install was not sent\n${lines.map(item => item.name || item.kind).join(',')}\n${output}`);
  assert.equal(install.arguments.gguf_dir, model);
  assert.deepEqual(calls(), [], `an offline screen called out\n${calls().join('\n')}\n${output}`);
});
