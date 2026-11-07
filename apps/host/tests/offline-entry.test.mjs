/** The Windows offline entry answers from local files. These tests do not start an engine or open a socket. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { isOfflineEntry, localFirstRun, localUpdateReport, offlineNetworkRefusal } from '../server/offline-entry.mjs';
import { createEngineStep } from '../server/engine-step.mjs';

const server = fileURLToPath(new URL('../server/', import.meta.url));

test('a missing engine is named and the update feed is not described as a next step', () => {
  assert.equal(isOfflineEntry({}), false);
  assert.equal(isOfflineEntry({ STRATA_OFFLINE_ENTRY: '1' }), true);
  const missing = localFirstRun('/opt/strata/components', () => false, () => []);
  assert.equal(missing.status, 'missing-local');
  assert.match(missing.message, /engine-component\.json/);
  assert.match(missing.message, /Nothing was downloaded/);
  assert.doesNotMatch(missing.message, /github|WebView2|winget|download the/i);
  const present = localFirstRun('/opt/strata/components', () => true, () => ['0.1.55-windows']);
  assert.equal(present.status, 'installed');
  const update = localUpdateReport('2026-11-07T00:00:00.000Z');
  assert.equal(update.checkedAt, '2026-11-07T00:00:00.000Z');
  assert.equal(update.feed, null);
  assert.match(update.strata.message, /Nothing was contacted/);
  assert.match(offlineNetworkRefusal('update-engine'), /Nothing was contacted/);
});

test('the host routes consult the offline entry before the update feed', () => {
  const index = readFileSync(join(server, 'index.mjs'), 'utf8');
  const first = index.indexOf("path === '/api/engine/first-run'");
  const gate = index.indexOf('isOfflineEntry()', first);
  const online = index.indexOf('engineFirstRun(', first);
  assert.ok(first > 0 && gate > first && gate < online, 'first-run must refuse the feed before asking it');
  const updates = index.indexOf("path === '/api/updates/check'");
  assert.ok(index.indexOf('isOfflineEntry()', updates) < index.indexOf('checkComponentUpdates(', updates));
  const native = readFileSync(join(server, 'native-control.mjs'), 'utf8');
  assert.match(native, /STRATA_OFFLINE_ENTRY==='1'&&\(kind==='update-engine'\|\|kind==='update-strata'\)/);
  assert.match(native, /STRATA_OFFLINE_MODEL/);
});

test('web search does not fetch while the offline entry is set', async () => {
  const previous = process.env.STRATA_OFFLINE_ENTRY;
  process.env.STRATA_OFFLINE_ENTRY = '1';
  try {
    const { apply } = await import('../server/agent/plugins/strata-search.mjs');
    let provider;
    apply({ web: { registerSearchProvider(value) { provider = value; } } }, { provider: 'duckduckgo' });
    await assert.rejects(() => provider.search({ query: 'local model' }), /Nothing was contacted/);
    assert.equal(provider.available(), false);
  } finally {
    if (previous === undefined) delete process.env.STRATA_OFFLINE_ENTRY;
    else process.env.STRATA_OFFLINE_ENTRY = previous;
  }
});

test('an install job does not ask the update feed when the offline entry is set', async () => {
  const previous = process.env.STRATA_OFFLINE_ENTRY;
  process.env.STRATA_OFFLINE_ENTRY = '1';
  let contacted = 0;
  try {
    const step = createEngineStep({
      control: {
        async engine() { return { usable: false }; },
        async updateCheck() { contacted++; return {}; },
        async update() { contacted++; return {}; },
      },
    });
    const part = await step.run();
    assert.equal(contacted, 0);
    assert.equal(part.state, 'failed');
    assert.match(part.message, /Nothing was downloaded/);
    assert.equal(part.retry, undefined);
  } finally {
    if (previous === undefined) delete process.env.STRATA_OFFLINE_ENTRY;
    else process.env.STRATA_OFFLINE_ENTRY = previous;
  }
});
