import View from 'ol/View.js';

export const WORLD_EXTENT = [-180, -90, 180, 90];

/** Navigation bounds are independent of each weather product's footprint. */
export function createMapView(extent: number[] = WORLD_EXTENT) {
  return new View({projection:'EPSG:4326', center:[104,33], zoom:4,
    minZoom:0, maxZoom:11, extent, showFullExtent:true});
}
