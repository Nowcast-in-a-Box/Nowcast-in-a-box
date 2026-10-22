import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
import {PlaybackPacer, nextFrame} from '../src/playback.ts';

// Execute the REAL Workspace (transpiled from src/App.tsx) on a minimal
// deterministic hook host; MapController is replaced by a controlled
// selection boundary. What runs here is the production component wiring —
// real handlers and effects — not a browser, pixel or timing claim. This is
// the seam the card-02 review used for its App-level findings. The evaluated
// code is this project's own App.tsx read from disk and transpiled with the
// project's own TypeScript; nothing from user or network input is evaluated.
const require = createRequire(new URL('../package.json', import.meta.url));
const ts = require('typescript');
const source = fs.readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
  .replace(/^import .*;\n/gm, '');
const js = ts.transpileModule(source,
  {compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.React}},
).outputText;

const makeWorkspace = () => {
  let hooks = [], cursor = 0, scheduled = true, effects = [], tree, controller;
  const useState = initial => { const i = cursor++;
    if (!hooks[i]) hooks[i] = {v: typeof initial === 'function' ? initial() : initial};
    return [hooks[i].v, v => { v = typeof v === 'function' ? v(hooks[i].v) : v;
      if (!Object.is(v, hooks[i].v)) { hooks[i].v = v; scheduled = true; } }]; };
  const useRef = initial => { const i = cursor++;
    if (!hooks[i]) hooks[i] = {current: initial}; return hooks[i]; };
  const useEffect = (fn, deps) => { const i = cursor++; const old = hooks[i];
    const changed = !old || !deps || !old.deps || deps.some((v, k) => !Object.is(v, old.deps[k]));
    if (changed) effects.push(() => { old?.cleanup?.(); hooks[i] = {deps, cleanup: fn()}; }); };
  const React = {createElement: (type, props, ...children) => ({type, props: props ?? {}, children})};
  class ControlledController {
    constructor(target, bundle, onError) { this.onError = onError; this.revision = 0; this.requests = []; this.cancels = 0; controller = this; }
    setThreshold() {} setLayer() {} resize() {} dispose() {}
    select(index, commit, opts) { const revision = ++this.revision;
      return new Promise((resolve, reject) => this.requests.push({index, opts, revision, commit, resolve, reject})); }
    cancelPending() { ++this.revision; this.cancels++; }
    finish(req) { const current = req.revision === this.revision; if (current) req.commit(); req.resolve(current); }
  }
  const iconNames = ['ArrowLineLeft', 'ArrowLineRight', 'CaretDown', 'CaretLeft', 'CaretRight', 'DownloadSimple', 'Info', 'Minus', 'Pause', 'Play', 'Plus', 'CornersOut', 'X', 'CloudSun', 'Mountains', 'GlobeHemisphereEast', 'SlidersHorizontal', 'Database'];
  const Workspace = new Function('React', 'useState', 'useRef', 'useEffect', 'MapController', 'PlaybackPacer', 'nextFrame', 'rgb', 'ResizeObserver', 'window', ...iconNames,
    js.replace(/^export /gm, '') + '\nreturn Workspace;')(
    React, useState, useRef, useEffect, ControlledController, PlaybackPacer, nextFrame, c => c,
    class {observe() {} disconnect() {}}, {addEventListener() {}, removeEventListener() {}},
    ...iconNames.map(() => () => null));
  const bundle = {frames: Array.from({length: 50}, (_, i) => ({
      valid_time: `2024-07-05T${String(Math.floor(i * 6 / 60)).padStart(2, '0')}:${String(i * 6 % 60).padStart(2, '0')}:00Z`,
      lead_seconds: i * 360})),
    init_time: '2024-07-05T00:00:00Z', shape: [100, 100], weather_style: {colors: [[1, 0, 0]]}};
  const walk = (node, fn) => { if (!node || typeof node !== 'object') return;
    if (Array.isArray(node)) { node.forEach(n => walk(n, fn)); return; }
    fn(node); node.children?.forEach(n => walk(n, fn)); };
  const assignRefs = () => walk(tree, node => {
    if (node.props.ref && !node.props.ref.current)
      node.props.ref.current = node.type === 'canvas'
        ? {width: 520, height: 28, getContext: () => ({fillRect() {}, set fillStyle(v) {}})} : {click() {}}; });
  const flush = () => { let n = 0;
    while (scheduled) { assert.ok(++n < 30, 'effect host did not settle'); scheduled = false; cursor = 0; effects = [];
      tree = Workspace({bundle}); assignRefs(); for (const effect of effects) effect(); } };
  const find = predicate => { let found; walk(tree, n => { if (predicate(n)) found = n; });
    assert.ok(found, 'UI control not found'); return found; };
  const button = label => find(n => n.type === 'button' && n.props['aria-label'] === label);
  const exportButton = () => find(n => n.type === 'button' && n.props.className === 'export');
  const retryButton = () => find(n => n.type === 'button' && n.children?.[0] === 'Retry');
  const slider = () => find(n => n.props['aria-label'] === 'Timeline');
  const speedControl = () => find(n => n.type === 'select' && n.props['aria-label'] === 'Playback speed');
  const sliderValue = () => slider().props.value;
  const frameCount = () => find(n => n.props.className === 'frame-count').children[0];
  const alert = () => { let found; walk(tree, n => { if (n.props.role === 'alert') found = n; }); return found; };
  return {controller: () => controller, flush, button, exportButton, retryButton, slider, sliderValue, speedControl, frameCount, alert};
};

test('SP-01: pausing keeps an explicit manual selection; it still commits while paused', () => {
  const w = makeWorkspace();
  w.flush(); const c = w.controller();
  c.finish(c.requests.at(-1)); w.flush();          // initial frame 0 committed, paused
  const base = c.requests.length;
  w.button('Next frame').props.onClick(); w.flush();   // explicit manual B=1
  const manual = c.requests.at(-1);
  assert.equal(manual.index, 1);
  assert.equal(manual.opts?.delayMs, undefined, 'a manual selection carries no deadline');
  w.button('Play').props.onClick(); w.flush();     // start: no new automatic request while loading
  assert.equal(c.requests.length, base + 1);
  w.button('Pause').props.onClick(); w.flush();     // pause must NOT cancel the manual selection
  assert.equal(c.cancels, 0, 'pause cancelled an explicit manual selection');
  assert.equal(manual.revision, c.revision, 'pause invalidated the manual request');
  assert.equal(w.sliderValue(), 1, 'pause rolled the explicit selection back');
  c.finish(manual); w.flush();                     // B completes while paused
  assert.equal(w.frameCount(), 2);
  assert.equal(w.exportButton().props.disabled, false, 'export stayed disabled after the manual commit');
});

test('SP-01: pausing still cancels the pending AUTOMATIC frame and rolls back to the displayed frame', () => {
  const w = makeWorkspace();
  w.flush(); const c = w.controller();
  c.finish(c.requests.at(-1)); w.flush();
  w.button('Play').props.onClick(); w.flush();
  const auto = c.requests.at(-1);
  assert.equal(auto.index, 1);
  assert.equal(typeof auto.opts?.delayMs, 'number', 'the automatic selection carries the pacer deadline');
  w.button('Pause').props.onClick(); w.flush();
  assert.equal(c.cancels, 1);
  assert.notEqual(auto.revision, c.revision, 'the auto frame stayed valid after pause');
  assert.equal(w.sliderValue(), 0);
  assert.equal(w.exportButton().props.disabled, false, 'cancelling the auto frame left loading stuck');
});

test('SP-01: a manual step preempts the automatic frame; a later pause keeps the manual request', () => {
  const w = makeWorkspace();
  w.flush(); const c = w.controller();
  c.finish(c.requests.at(-1)); w.flush();
  w.button('Play').props.onClick(); w.flush();
  const auto = c.requests.at(-1);                  // automatic frame 1 preparing
  w.button('Next frame').props.onClick(); w.flush();   // manual preempts it AND pauses
  const manual = c.requests.at(-1);
  assert.equal(manual.index, 2);
  assert.notEqual(auto.revision, c.revision, 'the preempted auto selection stayed current');
  w.button('Play').props.onClick(); w.flush();     // start again while the manual frame still prepares
  assert.equal(c.requests.length, 3, 'starting issued another automatic request over the manual one');
  w.button('Pause').props.onClick(); w.flush();     // pause must keep the explicit manual selection
  assert.equal(c.cancels, 0, 'pause cancelled the manual selection that preempted the auto frame');
  c.finish(manual); w.flush();
  assert.equal(w.frameCount(), 3);
});

test('SP-03: an explicit retry re-enters the selection for the displayed frame and clears the error', () => {
  const w = makeWorkspace();
  w.flush(); const c = w.controller();
  c.finish(c.requests.at(-1)); w.flush();          // frame 0 displayed, paused, not loading
  // A late failure of the displayed frame's own source raises the error with
  // selected===displayed and loading=false — the exact retry entry condition.
  c.onError('Failed to load map data. The current view is not fully loaded.'); w.flush();
  assert.ok(w.alert(), 'the error did not surface');
  const before = c.requests.length;
  w.retryButton().props.onClick(); w.flush();
  assert.equal(c.requests.length, before + 1, 'retry did not issue a new selection');
  assert.equal(c.requests.at(-1).index, 0);
  c.finish(c.requests.at(-1)); w.flush();
  assert.equal(w.alert(), undefined, 'the error banner survived a successful retry');
  assert.equal(w.exportButton().props.disabled, false);
});

test('a duplicate selection of the displayed frame without an error stays a no-op', () => {
  const w = makeWorkspace();
  w.flush(); const c = w.controller();
  c.finish(c.requests.at(-1)); w.flush();
  const before = c.requests.length;
  w.slider().props.onChange({target: {value: '0'}});   // re-pick the displayed frame
  w.flush();
  assert.equal(c.requests.length, before, 'a no-op duplicate selection issued a request');
});

test('SP-01: a speed switch while a manual selection prepares keeps it manual; pause still does not cancel it', () => {
  const w = makeWorkspace();
  w.flush(); const c = w.controller();
  c.finish(c.requests.at(-1)); w.flush();          // frame 0 displayed, paused
  w.button('Next frame').props.onClick(); w.flush();   // explicit manual B=1 preparing
  const manual = c.requests.at(-1);
  w.button('Play').props.onClick(); w.flush();     // start; the manual frame stays in flight
  w.speedControl().props.onChange({target: {value: '2'}}); w.flush();   // 2× while the manual frame prepares
  assert.equal(c.requests.length, 2, 'the speed switch re-issued the explicit manual selection as an automatic beat');
  assert.equal(manual.revision, c.revision, 'the speed switch superseded the manual selection');
  assert.equal(manual.opts?.delayMs, undefined, 'the speed switch attached a deadline to the manual selection');
  w.button('Pause').props.onClick(); w.flush();     // pause after the speed switch
  assert.equal(c.cancels, 0, 'pause after a speed switch cancelled the manual selection');
  assert.equal(w.sliderValue(), 1, 'pause after a speed switch rolled the manual selection back');
  assert.equal(w.exportButton().props.disabled, true, 'export re-enabled while the manual frame was still pending');
  c.finish(manual); w.flush();                     // B completes while paused
  assert.equal(w.frameCount(), 2);
  assert.equal(w.exportButton().props.disabled, false);
});

test('SP-01: after a speed switch during a manual preparation, playback continues from the manual commit at the new speed', () => {
  const w = makeWorkspace();
  w.flush(); const c = w.controller();
  c.finish(c.requests.at(-1)); w.flush();
  w.button('Next frame').props.onClick(); w.flush();   // explicit manual B=1 preparing
  const manual = c.requests.at(-1);
  w.button('Play').props.onClick(); w.flush();
  w.speedControl().props.onChange({target: {value: '2'}}); w.flush();   // 2× while the manual frame prepares
  assert.equal(c.requests.length, 2, 'the speed switch re-issued the pending manual selection');
  c.finish(manual); w.flush();                     // the manual frame commits while playing
  assert.equal(w.frameCount(), 2);
  assert.equal(c.requests.length, 3, 'the manual commit prepared more than the single next frame');
  const auto = c.requests.at(-1);
  assert.equal(auto.index, 2);
  // The follow-up beat uses the NEW speed anchored at the manual frame's
  // actual commit: a full fresh interval, chasing neither the old speed's
  // leftover interval nor a pre-switch deadline.
  assert.equal(typeof auto.opts?.delayMs, 'number', 'the follow-up automatic selection carries no deadline');
  assert.ok(auto.opts.delayMs > 50 && auto.opts.delayMs <= 100,
    `the 2× follow-up was not a fresh full interval (delayMs=${auto.opts.delayMs})`);
  w.button('Pause').props.onClick(); w.flush();     // that follow-up stays cancellable
  assert.equal(c.cancels, 1);
  assert.equal(w.sliderValue(), 1);
});

test('a speed switch still re-issues and keeps cancellable a pending AUTOMATIC selection', () => {
  const w = makeWorkspace();
  w.flush(); const c = w.controller();
  c.finish(c.requests.at(-1)); w.flush();
  w.button('Play').props.onClick(); w.flush();     // automatic frame 1 preparing at 1×
  const first = c.requests.at(-1);
  w.speedControl().props.onChange({target: {value: '4'}}); w.flush();
  assert.equal(c.requests.length, 3, 'the speed switch did not re-issue the automatic beat');
  const reissued = c.requests.at(-1);
  assert.equal(reissued.index, 1);
  assert.notEqual(first.revision, c.revision, 'the superseded automatic selection stayed current');
  assert.equal(typeof reissued.opts?.delayMs, 'number');
  assert.ok(reissued.opts.delayMs <= 50, `the 4× re-issue did not adopt the new interval (delayMs=${reissued.opts.delayMs})`);
  w.button('Pause').props.onClick(); w.flush();
  assert.equal(c.cancels, 1);
  assert.equal(w.sliderValue(), 0);
  assert.equal(w.exportButton().props.disabled, false);
});

test('an old effect callback after a speed beat cannot clear the new automatic origin', () => {
  const w = makeWorkspace();
  w.flush(); const c = w.controller();
  c.finish(c.requests.at(-1)); w.flush();
  w.button('Play').props.onClick(); w.flush();
  const old = c.requests.at(-1);
  w.speedControl().props.onChange({target: {value: '4'}}); w.flush();
  const current = c.requests.at(-1);
  assert.equal(current.index, 1);
  old.commit(); w.flush();   // late delivery from the superseded effect; its active flag must ignore it
  assert.equal(w.frameCount(), 1, 'a cleaned-up effect still changed displayed');
  w.button('Pause').props.onClick(); w.flush();
  assert.equal(c.cancels, 1, 'the old callback stripped the current automatic origin');
  assert.notEqual(current.revision, c.revision);
});

test('after a manual commit while playing, only the next automatic selection is cancellable again', () => {
  const w = makeWorkspace();
  w.flush(); const c = w.controller();
  c.finish(c.requests.at(-1)); w.flush();
  w.button('Next frame').props.onClick(); w.flush();
  const manual = c.requests.at(-1);
  w.button('Play').props.onClick(); w.flush();
  c.finish(manual); w.flush();                     // manual frame 1 commits while playing
  const auto = c.requests.at(-1);
  assert.equal(auto.index, 2);
  assert.equal(typeof auto.opts?.delayMs, 'number');
  assert.equal(w.frameCount(), 2);
  w.button('Pause').props.onClick(); w.flush();
  assert.equal(c.cancels, 1, 'the automatic follow-up after a manual commit was not cancellable');
  assert.equal(w.sliderValue(), 1);
});
