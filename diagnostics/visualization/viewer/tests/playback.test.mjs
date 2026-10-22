import test from 'node:test';
import assert from 'node:assert/strict';
import {awaitStableRender, intervalFor, nextFrame, PlaybackPacer, SelectionGate} from '../src/playback.ts';

test('animation loops over actual frame count, including a single frame', () => {
  assert.equal(nextFrame(1, 3), 2);
  assert.equal(nextFrame(2, 3), 0);
  assert.equal(nextFrame(0, 1), 0);
});

test('fast scrubbing never lets an older asynchronous frame overwrite the latest selection', async () => {
  const gate = new SelectionGate();
  const commits = [];
  let finishOld;
  const old = gate.run(() => new Promise(resolve => {finishOld = resolve}), v => commits.push(v));
  assert.equal(await gate.run(async () => 'new', v => commits.push(v)), true);
  finishOld('old');
  assert.equal(await old, false);
  assert.deepEqual(commits, ['new']);
});

test('the displayed frame is only replaced once the new preparation resolves', async () => {
  const gate = new SelectionGate();
  const commits = [];
  let ready;
  const run = gate.run(() => new Promise(resolve => {ready = resolve}), v => commits.push(v));
  await new Promise(resolve => setTimeout(resolve));
  assert.deepEqual(commits, []);
  ready('next');
  assert.equal(await run, true);
  assert.deepEqual(commits, ['next']);
});

test('a superseded frame that fails never surfaces an error or pauses playback', async () => {
  const gate = new SelectionGate();
  let failOld;
  const old = gate.run(() => new Promise((_, reject) => {failOld = reject}),
    () => assert.fail('superseded frame committed'));
  await gate.run(async () => 'current', () => {});
  failOld(new Error('stale tile failure'));
  assert.equal(await old, false);
});

test('the current selection still reports its own failure for retry', async () => {
  const gate = new SelectionGate();
  await assert.rejects(gate.run(async () => {throw new Error('Failed to load the weather frame');}, () => {}),
    /Failed to load the weather frame/);
});

test('a render is a commit point only when the view it rendered is still current', async () => {
  const views = ['before-pan', 'after-pan', 'after-pan'];
  let reads = 0, renders = 0;
  const view = () => views[Math.min(reads++, views.length - 1)];
  await awaitStableRender(view, async () => {renders++});
  assert.equal(renders, 2);
});

test('one render is enough when the view never changed while preparing', async () => {
  let renders = 0;
  await awaitStableRender(() => 'same-view', async () => {renders++});
  assert.equal(renders, 1);
});

test('successive view changes keep waiting until the view settles', async () => {
  const views = ['v1', 'v2', 'v3', 'v3'];
  let reads = 0, renders = 0;
  const view = () => views[Math.min(reads++, views.length - 1)];
  await awaitStableRender(view, async () => {renders++});
  assert.equal(renders, 3);
});

test('a stop signal ends the render wait loop instead of leaving it suspended', async () => {
  let rejectStop;
  const stop = new Promise((_, reject) => {rejectStop = reject;});
  const waiting = awaitStableRender(() => 'view', () => new Promise(() => {}), stop);
  rejectStop(new Error('Map loading timed out. Please retry.'));
  const outcome = await Promise.race([
    waiting.then(() => 'settled', () => 'settled'),
    new Promise(resolve => setTimeout(() => resolve('hung: loop left suspended'), 100)),
  ]);
  assert.equal(outcome, 'settled');
});

// A controllable clock: the pacer only ever asks what time it is now.
const clock = start => { let now = start; return {now: () => now, advance: ms => { now += ms; }}; };

test('speed options map to the 5fps-based target intervals', () => {
  assert.equal(intervalFor(.5), 400);
  assert.equal(intervalFor(1), 200);
  assert.equal(intervalFor(2), 100);
  assert.equal(intervalFor(4), 50);
});

test('the next frame is due one interval after the last actual commit', () => {
  const t = clock(1000);
  const pacer = new PlaybackPacer(t.now);
  t.advance(40); pacer.noteCommit();   // A actually committed at 1040
  t.advance(10);                       // selection of B starts at 1050
  assert.equal(pacer.commitDelay(1), 190);   // B is due at 1040 + 200, not at 1050 + 200
});

test('a late commit shifts the beat instead of being chased', () => {
  const t = clock(0);
  const pacer = new PlaybackPacer(t.now);
  t.advance(260); pacer.noteCommit();  // B was late and committed at 260
  t.advance(5);
  assert.equal(pacer.commitDelay(1), 195);   // C is due at 460, not at 260 + 200 - nothing catches up
});

test('start, resume and speed changes reset the beat from now', () => {
  const t = clock(1000);
  const pacer = new PlaybackPacer(t.now);
  t.advance(40); pacer.noteCommit();
  t.advance(5000); pacer.restart();    // paused for 5 s, then resumed at ~6040
  t.advance(10);
  assert.equal(pacer.commitDelay(1), 200);   // anchored at the resume time, not the old commit
});

test('a beat is never scheduled into the past', () => {
  const t = clock(0);
  const pacer = new PlaybackPacer(t.now);
  pacer.noteCommit();                  // committed at 0
  t.advance(1000);                     // the next selection starts far past the deadline
  assert.equal(pacer.commitDelay(4), 0);
});
