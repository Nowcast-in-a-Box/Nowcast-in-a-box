import VectorLayer from 'ol/layer/Vector.js';
import VectorTileLayer from 'ol/layer/VectorTile.js';
import VectorSource from 'ol/source/Vector.js';
import VectorTileSource from 'ol/source/VectorTile.js';
import VectorTile from 'ol/VectorTile.js';
import TileGrid from 'ol/tilegrid/TileGrid.js';
import TileState from 'ol/TileState.js';
import GeoJSON from 'ol/format/GeoJSON.js';
import MVT from 'ol/format/MVT.js';
import {Style, Stroke} from 'ol/style.js';
import type RenderFeature from 'ol/render/Feature.js';
import type {Bundle, BoundaryTiles} from './types.ts';

/** The mirror is sparse. A missing child uses the nearest physical ancestor. */
export function resolveWmoTile(keys: Set<string>, coord: number[]) {
  let [z, col, row] = coord;
  for (; z >= 0; z--, row = Math.floor(row / 2), col = Math.floor(col / 2)) {
    const key = `${z}/${row}/${col}`;
    if (keys.has(key)) return key;
  }
  return undefined;
}

export function wmoTileGrid(info: BoundaryTiles) {
  return new TileGrid({origin: info.origin, tileSize: info.tile_size,
    resolutions: info.resolutions, extent: [-180, -90, 180, 90]});
}

export function createBoundaryLayer(info: Bundle['boundaries'], onError: (message: string) => void) {
  const styles = Object.fromEntries(Object.entries(info.styles).map(([name, s]) => [name,
    new Style({stroke: new Stroke({color: s.edgecolor, width: s.linewidth,
      lineDash: s.linestyle === '--' ? [6, 4] : s.linestyle === ':' ? [2, 3] : undefined})}),
  ]));
  const unknown = new Style({stroke: new Stroke({color: '#A9ABAD', width: .6})});
  const error = () => onError('Failed to load boundaries. Check the local data bundle.');
  if (!info.tiles) {
    const source = new VectorSource({url: `/data/${info.url}`, format: new GeoJSON()});
    source.on('featuresloaderror', error);
    return new VectorLayer({source, className: 'boundary-layer',
      style: feature => styles[feature.get('boundary_type')] ?? unknown});
  }
  const tiles = info.tiles, keys = new Set(tiles.keys), grid = wmoTileGrid(tiles);
  const format = new MVT({layers: ['Boundaries']});
  const source = new VectorTileSource({projection: 'EPSG:4326', tileGrid: grid,
    format, wrapX: false, transition: 0,
    tileUrlFunction: coord => {
      const key = resolveWmoTile(keys, coord);
      return key === undefined ? undefined : `/data/${info.url}/${key}.pbf`;
    },
    tileLoadFunction: (rawTile, src) => {
      const tile = rawTile as VectorTile<RenderFeature>;
      // Decode in the PARENT's actual extent, never stretch its PBF to the
      // requested child's bounds. OL shares source tiles by their physical URL.
      const match = src.match(/\/(\d+)\/(\d+)\/(\d+)\.pbf$/)!;
      const [z, row, col] = match.slice(1).map(Number);
      const extent = grid.getTileCoordExtent([z, col, row]);
      tile.extent = extent;
      tile.resolution = grid.getResolution(z);
      tile.setLoader((_extent, _resolution, projection) => {
        void fetch(src).then(response => {
          if (!response.ok) throw new Error(`WMO tile HTTP ${response.status}`);
          return response.arrayBuffer();
        }).then(data => tile.setFeatures(format.readFeatures(data,
          {extent, featureProjection: projection})))
          .catch(() => tile.setState(TileState.ERROR));
      });
    },
  });
  source.on('tileloaderror', error);
  return new VectorTileLayer({source, className: 'boundary-layer', renderMode: 'vector',
    style: feature => styles[tiles.symbol_types[String(feature.get('_symbol'))]] ?? unknown});
}
