import type {Bundle} from './types';

export type MapIdentity = 'replay' | 'forecast';

function parseUrl(value: string, base: string) {
  try {
    return new URL(value, base);
  } catch {
    return undefined;
  }
}

/** Absolute same-origin bundle URL. Relative demo paths resolve against the page. */
export function resolveBundleUrl(bundleUrl: string) {
  if (typeof bundleUrl !== 'string' || !bundleUrl.trim())
    throw new Error('A map bundle URL is required.');
  const pageLocation = globalThis.location;
  const page = pageLocation?.href || 'http://localhost/';
  const resolved = parseUrl(bundleUrl, page);
  if (!resolved || (resolved.protocol !== 'http:' && resolved.protocol !== 'https:'))
    throw new Error('The map bundle URL is not usable.');
  const origin = pageLocation?.origin ?? '';
  if (origin && resolved.origin !== origin)
    throw new Error('The map bundle must be same-origin.');
  return resolved.href;
}

/**
 * Resolve one bundle asset against the viewer.json URL.
 * Root-absolute paths stay on that origin (`/map-basemap/...`).
 * Relative paths stay beside that bundle, never under a hardcoded `/data/`.
 */
export function resolveAssetUrl(assetUrl: string, bundleUrl: string) {
  if (typeof assetUrl !== 'string' || !assetUrl)
    throw new Error('A map asset URL is missing.');
  const base = new URL(resolveBundleUrl(bundleUrl));
  const resolved = parseUrl(assetUrl, base.href);
  if (!resolved || resolved.origin !== base.origin)
    throw new Error('Map assets must stay on the same origin as the bundle.');
  return resolved.href;
}

/** Matches the basemap cache layout: directory URL + "/" + key + ".pbf". */
export function boundaryTilePath(directoryUrl: string, key: string) {
  return `${directoryUrl.replace(/\/$/, '')}/${key}.pbf`;
}

function isNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function numbers(value: unknown, count: number) {
  return Array.isArray(value) && value.length >= count && value.every(isNumber);
}

function rgbTriple(value: unknown) {
  return Array.isArray(value) && value.length >= 3 && value.slice(0, 3).every(isNumber);
}

export function mapIdentity(bundle: {mock: boolean; is_real_forecast: boolean}): MapIdentity {
  if (bundle.mock === true && bundle.is_real_forecast === false) return 'replay';
  if (bundle.mock === false && bundle.is_real_forecast === true) return 'forecast';
  if (bundle.mock === true && bundle.is_real_forecast === true)
    throw new Error('This map bundle contradicts itself: it cannot be both an observation replay and a real forecast.');
  throw new Error('This map bundle has no supported identity. It must be an observation replay or a real forecast.');
}

export function validateBundle(raw: unknown): Bundle {
  if (!raw || typeof raw !== 'object') throw new Error('The map bundle is not valid.');
  const bundle = raw as Bundle;
  if (bundle.viewer_bundle_version !== 1) throw new Error('Unsupported map bundle version.');
  if (!Array.isArray(bundle.frames) || bundle.frames.length === 0)
    throw new Error('The map bundle has no frames.');
  for (const frame of bundle.frames) {
    if (!frame || typeof frame.url !== 'string' || !frame.url
        || typeof frame.valid_time !== 'string' || !frame.valid_time
        || !isNumber(frame.lead_seconds))
      throw new Error('A map frame is missing its time or file.');
  }
  if (!numbers(bundle.extent, 4) || bundle.extent.length !== 4)
    throw new Error('The map bundle is missing a geographic extent.');
  if (!numbers(bundle.shape, 2) || !numbers(bundle.native_shape, 2))
    throw new Error('The map bundle is missing grid shape.');
  const style = bundle.weather_style;
  if (!style || !isNumber(style.min) || !isNumber(style.max) || !isNumber(style.step)
      || !(style.max > style.min) || !(style.step > 0)
      || !Array.isArray(style.colors) || style.colors.length === 0 || !style.colors.every(rgbTriple)
      || !rgbTriple(style.over))
    throw new Error('The map bundle is missing a color style.');
  if (!bundle.terrain?.url || !Array.isArray(bundle.terrain.colors) || bundle.terrain.colors.length === 0
      || !bundle.boundaries?.url || !bundle.boundaries.styles || Array.isArray(bundle.boundaries.styles))
    throw new Error('The map bundle is missing terrain or boundaries.');
  if (bundle.boundaries.tiles) {
    const tiles = bundle.boundaries.tiles;
    if (!Array.isArray(tiles.keys) || !Array.isArray(tiles.resolutions) || !numbers(tiles.origin, 2)
        || !isNumber(tiles.tile_size) || !tiles.symbol_types)
      throw new Error('The map bundle is missing terrain or boundaries.');
  }
  for (const label of [bundle.title, bundle.notice, bundle.variable, bundle.units, bundle.init_time]) {
    if (typeof label !== 'string' || !label.trim())
      throw new Error('The map bundle is missing labels.');
  }
  mapIdentity(bundle);
  return bundle;
}

export function assertBundleAssets(bundle: Bundle, bundleUrl: string) {
  const resolved = resolveBundleUrl(bundleUrl);
  for (const frame of bundle.frames) resolveAssetUrl(frame.url, resolved);
  resolveAssetUrl(bundle.terrain.url, resolved);
  resolveAssetUrl(bundle.boundaries.url, resolved);
  for (const key of bundle.boundaries.tiles?.keys ?? [])
    resolveAssetUrl(boundaryTilePath(bundle.boundaries.url, key), resolved);
  return resolved;
}

export async function loadMapBundle(bundleUrl: string, signal?: AbortSignal) {
  const resolved = resolveBundleUrl(bundleUrl);
  const response = await fetch(resolved, {signal});
  if (!response.ok) throw new Error('The map data bundle is not ready.');
  const bundle = validateBundle(await response.json());
  assertBundleAssets(bundle, resolved);
  return {bundle, bundleUrl: resolved};
}

export function leadCaption(identity: MapIdentity) {
  return identity === 'replay' ? 'Replay offset' : 'Forecast lead';
}

export function initCaption(identity: MapIdentity) {
  return identity === 'replay' ? 'Replay reference' : 'Forecast init';
}

export function sourceIdentityNote(bundle: Bundle) {
  const notice = bundle.notice.replace(/\.\s*$/, '');
  if (mapIdentity(bundle) === 'replay')
    return `${notice}. Historical observations are replayed in time order; these are not model forecasts.`;
  return `${notice}. This is a model prediction, not an observation replay, and it does not establish forecast skill.`;
}

export function valueNotes(bundle: Bundle) {
  const shared = `Values below the current display threshold are transparent; this is not quality control. Units are ${bundle.units}. Playback speed only changes the viewing rate.`;
  if (mapIdentity(bundle) === 'replay')
    return `${shared} Special source codes have not been decoded.`;
  return `${shared} Masked cells stay empty. This view does not establish forecast skill.`;
}

export function exportIdentity(bundle: {mock: boolean; is_real_forecast: boolean}) {
  return mapIdentity(bundle) === 'replay'
    ? 'Observation replay, not a forecast'
    : 'Model prediction, not an observation replay; forecast skill is not validated';
}

export function formatScaleValue(value: number) {
  if (!Number.isFinite(value)) return '';
  const rounded = Math.round(value * 10) / 10;
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
}

/** Endpoints plus a short interior scale. Positions stay proportional to min/max. */
export function legendTicks(min: number, max: number) {
  if (!(max > min)) return [min];
  const span = max - min;
  const magnitude = 10 ** Math.floor(Math.log10(span / 6));
  const step = [1, 2, 2.5, 5, 10].map(factor => factor * magnitude).find(candidate => span / candidate <= 8)
    ?? magnitude * 10;
  const ticks: number[] = [];
  for (let value = Math.ceil((min + step * 1e-6) / step) * step; value < max - step * 0.25; value += step)
    ticks.push(Math.round(value * 1e6) / 1e6);
  return [min, ...ticks.filter(value => value > min && value < max), max];
}
