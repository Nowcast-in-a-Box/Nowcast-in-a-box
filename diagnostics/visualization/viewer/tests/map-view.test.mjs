import test from 'node:test';
import assert from 'node:assert/strict';
import {createMapView, WORLD_EXTENT} from '../src/map-view.ts';

test('navigation reaches regions outside the weather footprint', () => {
  const view=createMapView();
  view.setViewportSize([800,600]);
  view.setZoom(5);
  for (const centre of [[-120,35],[-60,-35],[150,-25],[10,50]]) {
    view.setCenter(centre);
    assert.deepEqual(view.getCenter(),centre);
  }
});

test('global fit contains both poles and dateline; weather fit remains separate', () => {
  const view=createMapView();
  const size=[800,600];
  view.setViewportSize(size);
  for (const target of [WORLD_EXTENT,[73,12,135,54]]) {
    view.fit(target,{size});
    const actual=view.calculateExtent(size);
    assert.ok(actual[0]<=target[0]+1e-8 && actual[1]<=target[1]+1e-8);
    assert.ok(actual[2]>=target[2]-1e-8 && actual[3]>=target[3]-1e-8);
  }
  assert.ok(view.getCenter()[0]>70);
});
