import {readFile, stat} from 'node:fs/promises';
import {join} from 'node:path';

export async function inspectViewerData(directory) {
  const bundle = JSON.parse(await readFile(join(directory, 'viewer.json'), 'utf8'));
  if (bundle.viewer_bundle_version !== 1 || !bundle.frames?.length) {
    throw new Error('viewer.json 不是可用的地图数据包；请先运行地图数据准备脚本。');
  }
  const paths = [bundle.terrain?.url, bundle.boundaries?.url, ...bundle.frames.map(frame => frame.url)];
  for (const path of paths) {
    if (typeof path !== 'string' || !(await stat(join(directory, path))).isFile()) {
      throw new Error(`地图数据文件不可用：${path}`);
    }
  }
  return bundle;
}
