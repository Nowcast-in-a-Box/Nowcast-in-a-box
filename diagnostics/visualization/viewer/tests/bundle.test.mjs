import test from 'node:test';
import assert from 'node:assert/strict';
import {get as getProjection} from 'ol/proj.js';
import {createBoundaryLayer} from '../src/boundary-layer.ts';
import {
  assertBundleAssets, boundaryTilePath, exportIdentity, leadCaption, legendTicks,
  loadMapBundle, mapIdentity, resolveAssetUrl, sourceIdentityNote, validateBundle,
} from '../src/bundle.ts';

const tiles = {origin: [-180, 90], tile_size: 512, resolutions: [1], keys: ['0/0/0', '7/13/67'], symbol_types: {}};
const style = {min: 10, max: 75, step: 0.5, colors: [[1, 0, 0]], over: [1, 1, 1]};

function bundle(identity) {
  return {
    viewer_bundle_version: 1,
    ...identity,
    title: 'CMA CREF', notice: 'Skill is not validated', variable: 'CREF', units: 'dBZ',
    init_time: '2024-07-05T00:00:00Z',
    frames: [{url: 'weather/frame-0000.tif', valid_time: '2024-07-05T00:06:00Z', lead_seconds: 360}],
    extent: [100, 20, 120, 40], shape: [40, 80], native_shape: [80, 160],
    weather_style: style,
    terrain: {url: '/map-basemap/terrain-land.tif', colors: [[0, '#fff']], attribution: 'ETOPO', disclaimer: 'terrain'},
    boundaries: {url: '/map-basemap/wmo', styles: {}, tiles, attribution: 'WMO', disclaimer: 'boundaries'},
  };
}

const runUrl = 'http://handler.example/visualization/attempt/viewer.json';

test('a real forecast is accepted and an observation replay stays distinct', () => {
  const forecast = validateBundle(bundle({mock: false, is_real_forecast: true}));
  const replay = validateBundle(bundle({mock: true, is_real_forecast: false}));
  assert.equal(mapIdentity(forecast), 'forecast');
  assert.equal(mapIdentity(replay), 'replay');
  assert.equal(leadCaption('forecast'), 'Forecast lead');
  assert.equal(leadCaption('replay'), 'Replay offset');
  assert.match(sourceIdentityNote(forecast), /not an observation replay/);
  assert.match(sourceIdentityNote(forecast), /does not establish forecast skill/);
  assert.doesNotMatch(sourceIdentityNote(forecast), /replayed in time order/);
  assert.match(sourceIdentityNote(replay), /not model forecasts/);
  assert.doesNotMatch(exportIdentity(forecast), /Observation replay/);
  assert.match(exportIdentity(replay), /not a forecast/);
});

test('contradictory and unknown identities are rejected', () => {
  assert.throws(() => validateBundle(bundle({mock: true, is_real_forecast: true})), /contradicts itself/);
  assert.throws(() => validateBundle(bundle({mock: false, is_real_forecast: false})), /no supported identity/);
  assert.throws(() => validateBundle({...bundle({mock: false, is_real_forecast: true}), frames: []}), /no frames/);
  assert.throws(() => validateBundle({...bundle({mock: false, is_real_forecast: true}), weather_style: {...style, colors: []}}), /color style/);
});

test('run weather stays beside viewer.json and basemap stays on /map-basemap/', () => {
  const forecast = validateBundle(bundle({mock: false, is_real_forecast: true}));
  assert.equal(resolveAssetUrl(forecast.frames[0].url, runUrl),
    'http://handler.example/visualization/attempt/weather/frame-0000.tif');
  assert.equal(resolveAssetUrl(forecast.terrain.url, runUrl),
    'http://handler.example/map-basemap/terrain-land.tif');
  assert.equal(resolveAssetUrl(boundaryTilePath(forecast.boundaries.url, '7/13/67'), runUrl),
    'http://handler.example/map-basemap/wmo/7/13/67.pbf');
  assert.equal(resolveAssetUrl('weather/frame-0000.tif', 'http://handler.example/data/viewer.json'),
    'http://handler.example/data/weather/frame-0000.tif');
  assert.throws(() => resolveAssetUrl('https://cdn.example/frame.tif', runUrl), /same origin/);
  assert.throws(() => resolveAssetUrl('//cdn.example/frame.tif', runUrl), /same origin/);
  assertBundleAssets(forecast, runUrl);
});

test('a page rejects a bundle whose origin is not its own', () => {
  const previous = globalThis.location;
  globalThis.location = {href: 'http://handler.example/results', origin: 'http://handler.example'};
  try {
    assert.throws(() => resolveAssetUrl('weather/frame-0000.tif', 'https://evil.example/viewer.json'), /same-origin/);
    assert.equal(resolveAssetUrl('weather/frame-0000.tif', '/visualization/attempt/viewer.json'),
      'http://handler.example/visualization/attempt/weather/frame-0000.tif');
  } finally {
    if (previous === undefined) delete globalThis.location;
    else globalThis.location = previous;
  }
});

test('boundary tiles use the bundle origin instead of a hardcoded /data/ prefix', () => {
  const layer = createBoundaryLayer(
    {url: '/map-basemap/wmo', tiles, styles: {}}, () => {}, runUrl);
  const projection = getProjection('EPSG:4326');
  const address = layer.getSource().getTileUrlFunction()([9, 270, 55], 1, projection);
  assert.equal(address, 'http://handler.example/map-basemap/wmo/7/13/67.pbf');
  assert.equal(String(address).includes('/data/'), false);
  layer.getSource().dispose();
  layer.dispose();
});

test('legend ticks follow the style range rather than a fixed dBZ scale', () => {
  assert.deepEqual(legendTicks(10, 75), [10, 20, 30, 40, 50, 60, 70, 75]);
  const raw = legendTicks(0, 255);
  assert.equal(raw[0], 0);
  assert.equal(raw.at(-1), 255);
  assert.ok(!raw.includes(75));
});

test('loadMapBundle fetches the given bundle URL and keeps its asset base', async () => {
  const forecast = bundle({mock: false, is_real_forecast: true});
  const calls = [];
  const previous = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    if (init?.signal?.aborted) throw Object.assign(new Error('The operation was aborted'), {name: 'AbortError'});
    calls.push({url, signal: init?.signal});
    return new Response(JSON.stringify(forecast), {status: 200});
  };
  try {
    const loaded = await loadMapBundle(runUrl);
    assert.deepEqual(calls.map(call => call.url), [runUrl]);
    assert.equal(loaded.bundleUrl, runUrl);
    assert.equal(loaded.bundle.variable, 'CREF');
    const controller = new AbortController();
    controller.abort();
    await assert.rejects(loadMapBundle(runUrl, controller.signal));
  } finally {
    globalThis.fetch = previous;
  }
});
