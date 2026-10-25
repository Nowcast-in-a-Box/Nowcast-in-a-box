import {readFile, stat} from 'node:fs/promises';
import {join} from 'node:path';

export async function inspectViewerData(directory) {
  const bundle = JSON.parse(await readFile(join(directory, 'viewer.json'), 'utf8'));
  if (bundle.viewer_bundle_version !== 1 || !bundle.frames?.length) {
    throw new Error('viewer.json is not a usable map bundle. Run the map data preparation script first.');
  }
  const boundaryPaths = bundle.boundaries?.tiles
    ? bundle.boundaries.tiles.keys.map(key => `${bundle.boundaries.url}/${key}.pbf`)
    : [bundle.boundaries?.url];
  const paths = [bundle.terrain?.url, ...boundaryPaths, ...bundle.frames.map(frame => frame.url)];
  for (const path of paths) {
    if (typeof path !== 'string' || !(await stat(join(directory, path))).isFile()) {
      throw new Error(`Map data file is unavailable: ${path}`);
    }
  }
  return bundle;
}
