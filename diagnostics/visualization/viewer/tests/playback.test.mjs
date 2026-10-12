import test from 'node:test';
import assert from 'node:assert/strict';
import {nextFrame, SelectionGate} from '../src/playback.ts';

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
