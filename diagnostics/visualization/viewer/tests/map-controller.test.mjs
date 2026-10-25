import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import Observable from 'ol/Observable.js';
import {SelectionGate} from '../src/playback.ts';

// Drive the REAL MapController.select/source/dispose without a DOM/WebGL map.
// Only the map, the weather layers, the GeoTIFF header reads and the window
// timers are test doubles, so the selection/cleanup code paths under test are
// the shipped ones. map.ts cannot be imported directly (extension-less TS
// specifiers), so it is transpiled into a generated module next to the tests
// (bare 'ol/…' specifiers still resolve through viewer/node_modules) with its
// three relative specifiers rewritten to the real source files.
const require = createRequire(new URL('.', import.meta.url));
const ts = require('typescript');
const src = new URL('../src/', import.meta.url);
const toFileUrl = name => pathToFileURL(new URL(name, src).pathname).href;
const rewriteSpecifiers = code => code
  .replace(/(from\s*['"])\.\/boundary-layer\.ts(['"])/g, `$1${toFileUrl('boundary-layer.ts')}$2`)
  .replace(/(from\s*['"])\.\/playback(['"])/g, `$1${toFileUrl('playback.ts')}$2`)
  .replace(/(from\s*['"])\.\/map-view(['"])/g, `$1${toFileUrl('map-view.ts')}$2`)
  .replace(/(from\s*['"])\.\/map-export(['"])/g, `$1${toFileUrl('map-export.ts')}$2`)
  .replace(/(from\s*['"])\.\/bundle(['"])/g, `$1${toFileUrl('bundle.ts')}$2`);
const generatedDir = new URL('./.cache/', import.meta.url);
fs.mkdirSync(generatedDir, {recursive: true});
const generated = path.join(generatedDir.pathname, 'map-controller.generated.mjs');
fs.writeFileSync(generated, ts.transpileModule(
  rewriteSpecifiers(fs.readFileSync(new URL('../src/map.ts', import.meta.url), 'utf8')),
  {compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext}},
).outputText);
const {MapController} = await import(pathToFileURL(generated).href);

// Controlled clock: select() schedules its 30s timeout through window.setTimeout.
let timerId = 0;
const timers = new Map();
globalThis.window = {
  setTimeout: (callback, ms) => { timers.set(++timerId, {callback, ms}); return timerId; },
  clearTimeout: id => { timers.delete(id); },
};
const fireTimer = id => { const entry = timers.get(id); if (entry) { timers.delete(id); entry.callback(); } };
const fireAllTimers = () => { for (const id of [...timers.keys()]) fireTimer(id); };
const settle = async () => { for (let i = 0; i < 16; i++) await Promise.resolve(); };

class FakeMap extends Observable {
  constructor() { super(); this.renderCalls = 0; this.resolution = 1; }
  getTargetElement() { return {dataset: {}}; }
  getView() { return {getCenter: () => [0, 0], getResolution: () => this.resolution, getRotation: () => 0}; }
  getSize() { return [800, 600]; }
  render() { this.renderCalls++; }
  setTarget() {}
  dispose() {}
}
class FakeLayer extends Observable {
  constructor() { super(); this.attached = null; this.visible = true; this.opacity = 1; }
  setSource(source) { this.attached = source; }
  getSource() { return this.attached; }
  getVisible() { return this.visible; }
  getOpacity() { return this.opacity; }
}
class FakeSource extends Observable {
  constructor(header = Promise.resolve({})) { super(); this.header = header; this.disposeCalls = 0; }
  getView() { return this.header; }
  dispose() { this.disposeCalls++; super.dispose(); }
}

const make = () => {
  const c = Object.create(MapController.prototype);
  const map = new FakeMap();
  const weatherLayers = [new FakeLayer(), new FakeLayer()];
  const errors = [];
  Object.assign(c, {
    map, sources: new Map(), gate: new SelectionGate(), revision: 0,
    weatherLayers, presented: 0, pendingSource: null, pendingFail: null,
    pendingTimer: undefined, pendingWaits: new Set(), latestIndex: 0, disposed: false,
    threshold: 10,
    bundleUrl: 'http://localhost/data/viewer.json',
    bundle: {frames: Array.from({length: 50}, (_, i) => ({url: `frame-${String(i).padStart(4, '0')}.tif`})), weather_style: {min: 10, max: 75, step: 0.5, units: 'dBZ'}},
    onError: message => errors.push(message),
  });
  return {c, map, prep: weatherLayers[1], errors};
};
// Fresh controllable sources per index; created sources are recorded in `made`.
const installSources = (c, headerFor) => {
  const made = [];
  c.getSource = index => {
    let source = c.sources.get(index);
    if (!source) { source = new FakeSource(headerFor(index)); made.push(source); c.sources.set(index, source); }
    return source;
  };
  return made;
};

test('F-01: a queued completion event from before the source swap cannot commit the new frame', async () => {
  const {c, map, prep} = make();
  installSources(c, () => Promise.resolve({}));
  let commits = 0;
  const done = c.select(7, () => commits++);
  await settle();
  assert.equal(commits, 0);
  // OpenLayers computes renderComplete_ during a render and dispatches it as a
  // 0ms task; a source swap in that gap keeps the old event deliverable. This
  // event belongs to the PREVIOUS frame: the new source is attached but the
  // preparing layer has not rendered it yet.
  map.dispatchEvent('rendercomplete');
  await settle();
  assert.equal(commits, 0);
  // The preparing layer actually draws the new source; a completion after
  // that frame may commit.
  prep.dispatchEvent('postrender');
  map.dispatchEvent('rendercomplete');
  assert.equal(await done, true);
  assert.equal(commits, 1);
});

test('F-01: hidden weather preparation commits on map-level completion (no layer render exists)', async () => {
  const {c, map, prep} = make();
  prep.visible = false;
  installSources(c, () => Promise.resolve({}));
  let commits = 0;
  const done = c.select(3, () => commits++);
  await settle();
  map.dispatchEvent('rendercomplete');
  assert.equal(await done, true);
  assert.equal(commits, 1);
});

test('F-02: repeated failed selections leave no rendercomplete listeners and release the failed source', async () => {
  const {c, map} = make();
  const made = installSources(c, () => Promise.resolve({}));
  for (let round = 0; round < 3; round++) {
    const failed = c.select(5, () => assert.fail('failed selection committed')).catch(error => error.message);
    await settle();
    fireTimer([...timers.keys()].at(-1));
    assert.equal(await failed, 'Map loading timed out. Please retry.');
    assert.equal(map.getListeners('rendercomplete')?.length ?? 0, 0,
      `rendercomplete listeners survived failure #${round + 1}`);
  }
  // The failed source left the cache, was detached from the preparing layer
  // and was actually disposed — not merely dropped from the registry.
  assert.equal(c.sources.has(5), false);
  assert.equal(c.preparingWeather().getSource(), null);
  assert.deepEqual(made.map(source => source.disposeCalls), [1, 1, 1]);
});

test('a superseded selection’s end never disarms the current selection’s timeout or failure path', async () => {
  const {c} = make();
  installSources(c, index => index === 6 ? new Promise(() => {}) : Promise.resolve({}));
  const old = c.select(6, () => assert.fail('superseded commit')).then(() => 'old-settled', error => error.message);
  await settle();
  const current = c.select(7, () => assert.fail('current commit before render')).then(() => 'cur-settled', error => error.message);
  await settle();
  const [oldTimer, currentTimer] = [...timers.keys()];
  fireTimer(oldTimer);      // the old selection times out first
  assert.equal(await old, 'old-settled');
  fireTimer(currentTimer);  // the current selection's own timeout must still work
  assert.equal(await current, 'Map loading timed out. Please retry.');
  assert.equal(timers.size, 0);
});

test('F-03: superseded hung-header selections do not accumulate cached sources', async () => {
  const {c} = make();
  installSources(c, () => new Promise(() => {}));
  const selections = [];
  for (let index = 0; index < 20; index++)
    selections.push(c.select(index, () => assert.fail('hung selection committed')).then(() => 'settled', error => error.message));
  await settle();
  fireAllTimers();
  await Promise.all(selections);
  const retained = [...c.sources.keys()];
  assert.ok(c.sources.size <= 7, `expected a bounded cache, retained ${JSON.stringify(retained)}`);
  for (const key of retained)
    assert.ok(Math.abs(key - 19) <= 2, `retained index ${key} is outside the ±2 neighbourhood of the latest request`);
});

test('F-04: terrain tile failures report immediately; stale weather failures stay silent', () => {
  const {c, errors} = make();
  const terrain = c.source('terrain/etopo.tif', 'terrain');
  terrain.dispatchEvent('tileloaderror');
  assert.equal(errors.length, 1);
  const stale = c.source('weather/frame-0042.tif');
  stale.dispatchEvent('tileloaderror');
  assert.equal(errors.length, 1);
  const pending = c.source('weather/frame-0000.tif');
  c.pendingSource = pending;
  pending.dispatchEvent('tileloaderror');
  assert.equal(errors.length, 2);
  const presented = c.source('weather/frame-0005.tif');
  c.presentedWeather().setSource(presented);
  presented.dispatchEvent('tileloaderror');
  assert.equal(errors.length, 3);
});

test('S-01: dispose immediately ends every live selection, not only the latest one', async () => {
  const {c} = make();
  installSources(c, () => new Promise(() => {}));
  const old = c.select(1, () => assert.fail('stale commit')).then(() => 'old-settled', error => error.message);
  await settle();
  const current = c.select(2, () => assert.fail('disposed commit')).then(() => 'cur-settled', error => error.message);
  await settle();
  c.dispose();
  const bounded = promise => Promise.race([promise,
    new Promise(resolve => setTimeout(() => resolve('__hung__'), 100))]);
  assert.deepEqual(await Promise.all([bounded(old), bounded(current)]), ['old-settled', 'cur-settled']);
  assert.equal(timers.size, 0, 'a disposed controller must not leave live timeout timers');
});

// R-01: B installs on the preparing layer, never draws its first frame, and is
// replaced by C. B's wait must end at the next map completion event (which its
// own armed listener receives) and flow into the unified cleanup — not keep
// re-arming listeners and requesting renders of an idle map until its own
// failure, its 30s timer or dispose.
test('R-01: a superseded render wait ends at the next completion event instead of re-arming', async () => {
  const {c, map, prep, errors} = make();
  installSources(c, () => Promise.resolve({}));
  const commits = [];
  const bounded = promise => Promise.race([promise,
    new Promise(resolve => setTimeout(() => resolve('__hung__'), 100))]);
  // B attaches and arms its render wait, but no postrender ever draws B.
  const b = c.select(10, () => commits.push('B')).then(() => 'b-settled', error => 'b-' + error.message);
  await settle();
  assert.equal(prep.getSource(), c.sources.get(10), 'B must be installed on the preparing layer');
  assert.equal(map.renderCalls, 1, 'B armed exactly one render request');
  // C replaces B before that first draw and arms its own wait.
  const current = c.select(11, () => commits.push('C')).then(() => 'c-settled', error => 'c-' + error.message);
  await settle();
  assert.equal(map.renderCalls, 2, 'the superseded wait must not request further renders while idle');
  // C draws and the map completes: C commits, B's wait must end here.
  prep.dispatchEvent('postrender');
  map.dispatchEvent('rendercomplete');
  await settle();
  assert.deepEqual(commits, ['C']);
  assert.equal(await current, 'c-settled');
  assert.equal(await bounded(b), 'b-settled');
  assert.equal(map.getListeners('rendercomplete')?.length ?? 0, 0, 'the superseded wait left a listener armed');
  assert.equal(c.pendingWaits.size, 0, 'the superseded selection stayed in pendingWaits');
  assert.equal(timers.size, 0, 'the superseded selection left its 30s timer live');
  assert.deepEqual(errors, [], 'the superseded selection surfaced an error');
  // Completions with nobody waiting must drive no renders and no registrations.
  const renders = map.renderCalls;
  map.dispatchEvent('rendercomplete');
  map.dispatchEvent('rendercomplete');
  await settle();
  assert.equal(map.renderCalls, renders, 'a completion with nobody waiting requested a render');
});

// Same sequence with the view signature changing while both waits are live:
// the current selection legitimately re-arms (its render was for the old
// view), but the superseded one must not re-enter waiting — its loop ends
// without registering or rendering again.
test('R-01: a superseded render wait does not re-enter waiting after a view change', async () => {
  const {c, map, prep} = make();
  installSources(c, () => Promise.resolve({}));
  const commits = [];
  const bounded = promise => Promise.race([promise,
    new Promise(resolve => setTimeout(() => resolve('__hung__'), 100))]);
  const b = c.select(10, () => commits.push('B')).then(() => 'b-settled', error => 'b-' + error.message);
  await settle();
  const current = c.select(11, () => commits.push('C')).then(() => 'c-settled', error => 'c-' + error.message);
  await settle();
  map.resolution = 2;   // the view both waits armed for is already stale
  prep.dispatchEvent('postrender');
  map.dispatchEvent('rendercomplete');   // wakes B (must end) and re-arms C (legitimate)
  await settle();
  assert.equal(await bounded(b), 'b-settled');
  assert.equal(map.getListeners('rendercomplete')?.length ?? 0, 1,
    'only the current selection may hold a listener after a view change');
  map.dispatchEvent('rendercomplete');   // C settles at the current view
  await settle();
  assert.deepEqual(commits, ['C']);
  assert.equal(await current, 'c-settled');
  assert.equal(map.getListeners('rendercomplete')?.length ?? 0, 0);
  assert.equal(c.pendingWaits.size, 0);
  assert.equal(timers.size, 0);
});

// --- Card 02: paced auto-playback -----------------------------------------
//
// The auto-advance passes {delayMs} so the next frame's preparation starts
// immediately after the previous commit while its commit is held until the
// deadline (last actual commit + target interval). cancelPending() models a
// pause: the pending auto frame can no longer commit in either stage.
const deadlineTimer = ms => {
  const entry = [...timers.entries()].find(([, timer]) => timer.ms === ms);
  assert.ok(entry, `no deadline timer of ${ms}ms is pending`);
  return entry[0];
};

test('02: a ready frame is held until its deadline, then commits', async () => {
  const {c, map, prep} = make();
  installSources(c, () => Promise.resolve({}));
  let commits = 0;
  const done = c.select(7, () => commits++, {delayMs: 200});
  await settle();
  prep.dispatchEvent('postrender');
  map.dispatchEvent('rendercomplete');
  await settle();
  assert.equal(commits, 0, 'a ready frame committed before its deadline');
  fireTimer(deadlineTimer(200));
  assert.equal(await done, true);
  assert.equal(commits, 1);
  assert.equal(timers.size, 0, 'the settled selection left its deadline/timeout timers live');
});

test('02: a frame that becomes ready after its deadline commits when ready, without a further wait', async () => {
  const {c, map, prep} = make();
  installSources(c, () => Promise.resolve({}));
  let commits = 0;
  const bounded = promise => Promise.race([promise,
    new Promise(resolve => setTimeout(() => resolve('__hung__'), 100))]);
  const done = c.select(7, () => commits++, {delayMs: 50});
  await settle();
  fireTimer(deadlineTimer(50));        // the deadline passes while still preparing
  await settle();
  assert.equal(commits, 0);
  prep.dispatchEvent('postrender');    // the frame only becomes ready now
  map.dispatchEvent('rendercomplete');
  assert.equal(await bounded(done), true);   // commits at readiness, no extra interval
  assert.equal(commits, 1);
  assert.equal(timers.size, 0);
});

test('02: a view change during the deadline wait re-validates before committing', async () => {
  const {c, map, prep} = make();
  installSources(c, () => Promise.resolve({}));
  let commits = 0;
  const done = c.select(7, () => commits++, {delayMs: 200});
  await settle();
  prep.dispatchEvent('postrender');
  map.dispatchEvent('rendercomplete');  // ready at the old view
  await settle();
  map.resolution = 2;                   // the user pans/zooms while the frame waits
  fireTimer(deadlineTimer(200));
  await settle();
  assert.equal(commits, 0, 'committed a frame prepared for a stale view');
  assert.equal(map.getListeners('rendercomplete')?.length ?? 0, 1,
    'the deadline re-validation must re-arm exactly one completion wait');
  prep.dispatchEvent('postrender');
  map.dispatchEvent('rendercomplete');
  assert.equal(await done, true);
  assert.equal(commits, 1);
  assert.equal(map.getListeners('rendercomplete')?.length ?? 0, 0);
  assert.equal(timers.size, 0);
});

test('02: pause during preparation — a late completion changes nothing', async () => {
  const {c, map, prep, errors} = make();
  installSources(c, () => Promise.resolve({}));
  const commits = [];
  const bounded = promise => Promise.race([promise,
    new Promise(resolve => setTimeout(() => resolve('__hung__'), 100))]);
  const auto = c.select(10, () => commits.push('B'), {delayMs: 200});
  await settle();
  c.cancelPending();                    // the user pauses while B is preparing
  prep.dispatchEvent('postrender');     // the completion arrives anyway
  map.dispatchEvent('rendercomplete');
  assert.equal(await bounded(auto), false);
  assert.deepEqual(commits, []);
  assert.deepEqual(errors, [], 'a cancelled auto frame surfaced an error');
  assert.equal(map.getListeners('rendercomplete')?.length ?? 0, 0);
  assert.equal(c.pendingWaits.size, 0);
  assert.equal(timers.size, 0);
});

test('02: pause while ready-and-waiting — passing the deadline keeps the displayed frame', async () => {
  const {c, map, prep, errors} = make();
  installSources(c, () => Promise.resolve({}));
  const commits = [];
  const bounded = promise => Promise.race([promise,
    new Promise(resolve => setTimeout(() => resolve('__hung__'), 100))]);
  const auto = c.select(10, () => commits.push('B'), {delayMs: 200});
  await settle();
  prep.dispatchEvent('postrender');
  map.dispatchEvent('rendercomplete');  // B is fully ready, only waiting for its deadline
  await settle();
  c.cancelPending();                    // the user pauses before the deadline
  fireTimer(deadlineTimer(200));        // the deadline passes anyway
  assert.equal(await bounded(auto), false);
  assert.deepEqual(commits, []);
  assert.deepEqual(errors, []);
  assert.equal(map.getListeners('rendercomplete')?.length ?? 0, 0);
  assert.equal(timers.size, 0);
});

test('02: a cancelled frame’s late failure stays silent and keeps the displayed source', async () => {
  const {c, map, prep, errors} = make();
  const made = installSources(c, () => Promise.resolve({}));
  const commits = [];
  const bounded = promise => Promise.race([promise,
    new Promise(resolve => setTimeout(() => resolve('__hung__'), 100))]);
  const auto = c.select(10, () => commits.push('B'), {delayMs: 200});
  await settle();
  prep.dispatchEvent('postrender');
  map.dispatchEvent('rendercomplete');
  await settle();
  c.cancelPending();
  made[0].dispatchEvent('tileloaderror');   // a late tile error for the cancelled frame
  assert.equal(await bounded(auto), false);
  assert.deepEqual(commits, []);
  assert.deepEqual(errors, [], 'cancelling an auto frame was reported as a load failure');
  assert.equal(c.sources.has(10), true, 'the cancelled frame’s source was discarded although it is still displayable');
  assert.equal(timers.size, 0);
});

test('02: a manual selection preempts the pending auto frame; its deadline never commits it', async () => {
  const {c, map, prep} = make();
  installSources(c, () => Promise.resolve({}));
  const commits = [];
  const bounded = promise => Promise.race([promise,
    new Promise(resolve => setTimeout(() => resolve('__hung__'), 100))]);
  const auto = c.select(7, () => commits.push('auto'), {delayMs: 200});
  await settle();
  const manual = c.select(8, () => commits.push('manual'));
  await settle();
  prep.dispatchEvent('postrender');
  map.dispatchEvent('rendercomplete');
  await settle();
  assert.equal(await bounded(manual), true);
  assert.equal(await bounded(auto), false);
  assert.deepEqual(commits, ['manual']);
  assert.equal(timers.size, 0, 'the superseded auto frame left its deadline timer live');
});

test('02: switching speed re-issues the beat — only the new deadline can commit', async () => {
  const {c, map, prep} = make();
  installSources(c, () => Promise.resolve({}));
  let commits = 0;
  const bounded = promise => Promise.race([promise,
    new Promise(resolve => setTimeout(() => resolve('__hung__'), 100))]);
  const slow = c.select(7, () => commits++, {delayMs: 200});
  await settle();
  const fast = c.select(7, () => commits++, {delayMs: 50});   // re-issued at the new speed
  await settle();
  prep.dispatchEvent('postrender');
  map.dispatchEvent('rendercomplete');
  await settle();
  assert.equal(commits, 0, 'committed before the new deadline');
  fireTimer(deadlineTimer(50));
  assert.equal(await bounded(fast), true);
  assert.equal(await bounded(slow), false);
  assert.equal(commits, 1);
  assert.equal(timers.size, 0);
});

test('02: a failure while waiting for the deadline is a real error for the current selection', async () => {
  const {c, map, prep, errors} = make();
  const made = installSources(c, () => Promise.resolve({}));
  let commits = 0;
  const done = c.select(7, () => commits++, {delayMs: 200}).then(() => 'committed', error => error.message);
  await settle();
  prep.dispatchEvent('postrender');
  map.dispatchEvent('rendercomplete');
  await settle();
  assert.equal(commits, 0);
  made[0].dispatchEvent('tileloaderror');   // the source genuinely fails before the deadline
  assert.equal(await done, 'Failed to load the weather frame');
  assert.equal(commits, 0);
  assert.equal(c.sources.has(7), false);
  assert.equal(made[0].disposeCalls, 1);
  assert.equal(timers.size, 0);
});

// --- Card 02 review fixes: cancellation ownership ---------------------------
//
// The review's ST-01/SP-02 ran through the PRODUCTION source() factory whose
// permanent tileloaderror handler is what leaked cancelled errors to the UI.
// FakeSource-based tests cannot see it, so these regressions create sources
// through the real c.source() (production handler installed) and only control
// the GeoTIFF header read — the network boundary.

const installRealSources = (c, headerFor) => {
  c.getSource = index => {
    let source = c.sources.get(index);
    if (!source) {
      source = c.source(`weather/frame-${String(index).padStart(4, '0')}.tif`, 'weather');
      source.getView = () => headerFor(index);
      c.sources.set(index, source);
    }
    return source;
  };
};

test('fix SP-02: a cancelled auto frame’s late tile error stays silent (production source handler)', async () => {
  const {c, map, prep, errors} = make();
  installRealSources(c, () => Promise.resolve({}));
  const commits = [];
  const bounded = promise => Promise.race([promise,
    new Promise(resolve => setTimeout(() => resolve('__hung__'), 100))]);
  // The presented layer shows A (frame 0) as a committed frame would.
  c.presentedWeather().setSource(c.getSource(0));
  const auto = c.select(1, () => commits.push('B'), {delayMs: 200});
  await settle();
  prep.dispatchEvent('postrender');
  map.dispatchEvent('rendercomplete');   // B fully ready, waiting for its deadline
  await settle();
  c.cancelPending();                     // pause cancels the auto frame
  c.getSource(1).dispatchEvent('tileloaderror');   // its in-flight tile really fails now
  assert.equal(await bounded(auto), false);
  assert.deepEqual(commits, []);
  assert.deepEqual(errors, [], 'the cancelled frame’s late error reached the UI through the permanent source handler');
  // The frame still on screen keeps its real error reporting.
  c.getSource(0).dispatchEvent('tileloaderror');
  assert.deepEqual(errors, ['Failed to load map data. The current view is not fully loaded.']);
  assert.equal(timers.size, 0);
});

test('fix: a cancelled selection’s cleanup never strips a newer selection’s error ownership of the same source', async () => {
  const {c, map, prep, errors} = make();
  installRealSources(c, () => Promise.resolve({}));
  const commits = [];
  const bounded = promise => Promise.race([promise,
    new Promise(resolve => setTimeout(() => resolve('__hung__'), 100))]);
  const cancelled = c.select(1, () => commits.push('B1'), {delayMs: 200});
  await settle();
  c.cancelPending();
  // Re-select the same index before the cancelled selection settles: the
  // cached source object is reused, and error ownership must move to the new
  // request rather than being cleared by the old selection's cleanup.
  const current = c.select(1, () => commits.push('B2'));
  await settle();
  // One completion settles only the cancelled wait (it is stale); the current
  // one has not drawn its first frame yet and legitimately re-arms.
  map.dispatchEvent('rendercomplete');
  await settle();
  assert.equal(await bounded(cancelled), false);
  assert.deepEqual(commits, []);
  // The current selection owns the source: its late tile error must report.
  c.getSource(1).dispatchEvent('tileloaderror');
  assert.deepEqual(errors, ['Failed to load map data. The current view is not fully loaded.']);
  await assert.rejects(bounded(current), /Failed to load the weather frame/);
  assert.equal(timers.size, 0);
});
