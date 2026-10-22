import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, existsSync} from 'node:fs';
import {gunzipSync} from 'node:zlib';
import MVT from 'ol/format/MVT.js';
import VectorTile from 'ol/VectorTile.js';
import TileState from 'ol/TileState.js';
import {get as getProjection} from 'ol/proj.js';
import {createBoundaryLayer, resolveWmoTile, wmoTileGrid} from '../src/boundary-layer.ts';

const info = {origin:[-180,90], tile_size:512,
  resolutions:Array.from({length:10}, (_,z)=>.703125 / 2 ** z),
  keys:['0/0/0','7/13/67','8/42/214'], symbol_types:{0:'coastline'}};

test('zoom selects finer local geometry; missing children keep their actual parent address', () => {
  const grid = wmoTileGrid(info), keys = new Set(info.keys);
  assert.equal(grid.getZForResolution(.3515625, 1), 1);
  assert.equal(grid.getZForResolution(.001373291015625, 1), 9);
  assert.equal(resolveWmoTile(keys, [9,270,55]), '7/13/67');
  assert.equal(resolveWmoTile(keys, [8,214,42]), '8/42/214');
  assert.equal(resolveWmoTile(keys, [4,10,3]), '0/0/0');
  assert.equal(resolveWmoTile(keys, [9,-1,0]), undefined);
});

const parentFile = new URL('../../data/cache/basemap/wmo/VectorTileServer/tile/7/13/67.pbf', import.meta.url);
test('actual parent PBF decodes at Germany, not stretched to the requested child',
  {skip: !existsSync(parentFile)}, async t => {
    const layer = createBoundaryLayer({url:'wmo', tiles:info, styles:{}}, ()=>{});
    const source = layer.getSource(), projection = getProjection('EPSG:4326');
    const address = source.getTileUrlFunction()([9,270,55], 1, projection);
    let bytes = readFileSync(parentFile);
    if (bytes[0] === 31 && bytes[1] === 139) bytes = gunzipSync(bytes);
    t.mock.method(globalThis, 'fetch', async url => {
      assert.equal(url, '/data/wmo/7/13/67.pbf');
      return new Response(bytes);
    });
    const tile = new VectorTile([9,270,55], TileState.IDLE, address,
      new MVT({layers:['Boundaries']}), source.getTileLoadFunction());
    tile.extent = wmoTileGrid(info).getTileCoordExtent([9,270,55]);
    tile.projection = projection; tile.resolution = info.resolutions[9];
    const done = new Promise((resolve, reject) => tile.addEventListener('change', () => {
      if (tile.getState() === TileState.LOADED) resolve();
      if (tile.getState() === TileState.ERROR) reject(new Error('tile decode failed'));
    }));
    tile.load(); await done;
    assert.deepEqual(tile.extent, [8.4375,50.625,11.25,53.4375]);
    const features = tile.getFeatures();
    assert.ok(features.length > 0);
    assert.ok(features.every(f => f.get('layer') === 'Boundaries'));
    const xs = features.flatMap(f => f.getFlatCoordinates().filter((_,i)=>i%2===0));
    const ys = features.flatMap(f => f.getFlatCoordinates().filter((_,i)=>i%2===1));
    assert.ok(Math.min(...xs) > 8.4 && Math.max(...xs) < 8.6, 'parent was stretched or clipped to a child');
    assert.ok(Math.min(...ys) > 53.3 && Math.max(...ys) < 53.6);
    source.dispose(); layer.dispose();
  });

test('a missing local tile reaches the error state rather than a false ready state', async t => {
  const layer = createBoundaryLayer({url:'wmo', tiles:info, styles:{}}, ()=>{});
  const source = layer.getSource();
  t.mock.method(globalThis, 'fetch', async () => new Response('', {status:404}));
  const tile = new VectorTile([0,0,0], TileState.IDLE, '/data/wmo/0/0/0.pbf',
    new MVT({layers:['Boundaries']}), source.getTileLoadFunction());
  tile.projection = getProjection('EPSG:4326');
  const done = new Promise(resolve => tile.addEventListener('change', () => {
    if (tile.getState() === TileState.ERROR) resolve();
  }));
  tile.load(); await done;
  assert.equal(tile.getFeatures(), null);
  source.dispose(); layer.dispose();
});
