import assert from 'node:assert/strict';
import {mkdtemp, mkdir, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {inspectViewerData} from '../scripts/viewer-data.mjs';

async function fixture(t) {
  const directory = await mkdtemp(join(tmpdir(), 'nib-viewer-start-'));
  t.after(() => rm(directory, {recursive:true, force:true}));
  const bundle = {viewer_bundle_version:1, frames:[{url:'weather/frame-0000.tif'}],
    terrain:{url:'terrain.tif'}, boundaries:{url:'boundaries.geojson'}};
  await mkdir(join(directory,'weather'));
  await writeFile(join(directory,'viewer.json'), JSON.stringify(bundle));
  for (const file of ['weather/frame-0000.tif','terrain.tif','boundaries.geojson']) {
    await writeFile(join(directory,file), 'file-presence fixture');
  }
  return {directory,bundle};
}

test('startup accepts a prepared bundle with every referenced file', async t => {
  const {directory,bundle} = await fixture(t);
  assert.deepEqual(await inspectViewerData(directory), bundle);
});

test('startup names a missing weather frame before serving the page', async t => {
  const {directory} = await fixture(t);
  await rm(join(directory,'weather/frame-0000.tif'));
  await assert.rejects(inspectViewerData(directory), /weather\/frame-0000\.tif/);
});

test('startup requires the manifest instead of serving a broken map', async t => {
  const {directory} = await fixture(t);
  await rm(join(directory,'viewer.json'));
  await assert.rejects(inspectViewerData(directory), /viewer\.json/);
});
