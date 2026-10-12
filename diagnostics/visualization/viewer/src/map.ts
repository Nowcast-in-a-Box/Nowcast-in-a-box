import Map from 'ol/Map.js';
import {createMapView, WORLD_EXTENT} from './map-view';
import GeoTIFF from 'ol/source/GeoTIFF.js';
import WebGLTileLayer from 'ol/layer/WebGLTile.js';
import VectorLayer from 'ol/layer/Vector.js';
import VectorSource from 'ol/source/Vector.js';
import GeoJSON from 'ol/format/GeoJSON.js';
import {ScaleLine} from 'ol/control.js';
import {Style, Stroke} from 'ol/style.js';
import {unByKey} from 'ol/Observable.js';
import type {ExpressionValue} from 'ol/expr/expression.js';
import type {EventsKey} from 'ol/events.js';
import type {Bundle, LayerName, LayerSetting} from './types';
import {SelectionGate} from './playback';
import {drawLayerCanvas} from './map-export';

const url = (path: string) => `/data/${path}`;
export const rgb = (c: number[]) => c.map(v => Math.round(v * 255));

export class MapController {
  readonly map: Map;
  readonly weather: WebGLTileLayer;
  readonly terrain: WebGLTileLayer;
  readonly boundaries: VectorLayer;
  private sources = new globalThis.Map<number, GeoTIFF>();
  private gate = new SelectionGate();
  private revision = 0;
  private onError: (message: string) => void;
  private bundle: Bundle;
  private threshold = 10;

  constructor(target: HTMLElement, bundle: Bundle, onError: (message: string) => void) {
    this.bundle = bundle;
    this.onError = onError;
    this.weather = new WebGLTileLayer({className: 'weather-layer', opacity: .96,
      style: this.weatherStyle(10), cacheSize: 128});
    const terrainSource = this.source(bundle.terrain.url);
    this.terrain = new WebGLTileLayer({className: 'terrain-layer', opacity: .56,
      source: terrainSource, style: {color: [
        'case', ['==', ['band', 2], 0], [0, 0, 0, 0],
        ['interpolate', ['linear'], ['band', 1], ...bundle.terrain.colors.flat()],
      ] as ExpressionValue}});
    const vectors = new VectorSource({url: url(bundle.boundaries.url), format: new GeoJSON()});
    vectors.on('featuresloaderror', () => onError('边界数据读取失败，请检查本地数据包。'));
    this.boundaries = new VectorLayer({source: vectors, className: 'boundary-layer', style: feature => {
      const s = bundle.boundaries.styles[feature.get('boundary_type')];
      return new Style({stroke: new Stroke({color: s?.edgecolor ?? '#A9ABAD',
        width: s?.linewidth ?? .6,
        lineDash: s?.linestyle === '--' ? [6, 4] : s?.linestyle === ':' ? [2, 3] : undefined})});
    }});
    this.map = new Map({target, layers: [this.terrain, this.weather, this.boundaries],
      controls: [new ScaleLine({units: 'metric', minWidth: 100})],
      view: createMapView(bundle.map_extent)});
    this.fit();
  }

  private source(path: string) {
    const source = new GeoTIFF({sources: [{url: url(path)}], normalize: false,
      interpolate: false, transition: 0, wrapX: false});
    source.on('tileloaderror', () => this.onError('地图数据读取失败；当前画面尚未完成加载。'));
    return source;
  }

  private getSource(index: number) {
    let source = this.sources.get(index);
    if (!source) { source = this.source(this.bundle.frames[index].url); this.sources.set(index, source); }
    return source;
  }

  private weatherStyle(threshold: number) {
    const s = this.bundle.weather_style;
    return {color: [
      'case', ['any', ['==', ['band', 2], 0], ['<', ['band', 1], threshold]], [0, 0, 0, 0],
      ['>', ['band', 1], s.max], [...rgb(s.over), 1],
      ['palette', ['clamp', ['floor', ['/', ['-', ['band', 1], s.min], s.step]],
        0, s.colors.length - 1], s.colors.map(c => [...rgb(c), 1])],
    ] as ExpressionValue};
  }

  setThreshold(value: number) { this.threshold = value; this.weather.setStyle(this.weatherStyle(value)); }
  setLayer(name: LayerName, settings: LayerSetting) {
    this[name].setVisible(settings.visible);
    this[name].setOpacity(settings.opacity);
  }
  fit() { this.map.getView().fit(this.bundle.extent, {padding: [24, 32, 40, 32]}); }
  fitWorld() { this.map.getView().fit(this.bundle.map_extent ?? WORLD_EXTENT, {padding: [20, 20, 20, 20]}); }
  zoom(delta: number) { const view = this.map.getView(); view.animate({zoom: (view.getZoom() ?? 4) + delta, duration: 180}); }
  resize() { this.map.updateSize(); }

  async select(index: number, commit: () => void) {
    const revision = ++this.revision;
    return this.gate.run(async () => {
      const source = this.getSource(index);
      await source.getView();
      if (revision !== this.revision) return;
      await new Promise<void>((resolve, reject) => {
        let key: EventsKey;
        const errorKey = source.once('tileloaderror', () => finish(new Error('天气帧读取失败')));
        const timer = window.setTimeout(() => finish(new Error('地图加载超时，请重试')), 30000);
        const finish = (error?: Error) => {
          clearTimeout(timer); unByKey(key); unByKey(errorKey);
          error ? reject(error) : resolve();
        };
        key = this.map.once('rendercomplete', () => finish());
        this.weather.setSource(source);
        this.map.render();
      });
    }, () => {
      commit();
      // Bounded source cache; time navigation does not retain the entire sequence.
      for (const [key, source] of this.sources) {
        if (Math.abs(key - index) > 2) { this.sources.delete(key); source.dispose(); }
      }
      const next = (index + 1) % this.bundle.frames.length;
      void this.getSource(next).getView().catch(() => {});
    });
  }

  exportPng(index: number) {
    this.map.renderSync();
    const size = this.map.getSize()!;
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d')!;
    const frame = this.bundle.frames[index];
    const wrap = (text: string, width: number) => {
      const lines: string[] = []; let line = '';
      for (const char of text) {
        if (line && ctx.measureText(line + char).width > width) {lines.push(line); line = '';}
        line += char;
      }
      if (line) lines.push(line);
      return lines;
    };
    ctx.font = 'bold 16px sans-serif';
    const title = wrap(`${this.bundle.title} | ${frame.valid_time} | ${this.bundle.notice}`, size[0] - 32);
    ctx.font = '12px sans-serif';
    const layerNote = `图层：天气${this.weather.getVisible() ? '开启' : '关闭'} / 地形${this.terrain.getVisible() ? '开启' : '关闭'} / 边界${this.boundaries.getVisible() ? '开启' : '关闭'}；最低显示值 ${this.threshold} dBZ。`;
    const notes = wrap(`${layerNote} 显示取样 ${this.bundle.shape[1]} × ${this.bundle.shape[0]}；当前视图截图。 ${this.bundle.boundaries.attribution} ${this.bundle.terrain.attribution} ${this.bundle.boundaries.disclaimer}`, size[0] - 32);
    const legendHeight = this.weather.getVisible() ? 48 : 0;
    const footerHeight = 30 + title.length * 22 + legendHeight + notes.length * 17;
    canvas.width = size[0]; canvas.height = size[1] + footerHeight;
    ctx.fillStyle = getComputedStyle(this.map.getTargetElement()!.parentElement!).backgroundColor;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    // Hidden OpenLayers canvases may retain old pixels: use layer visibility explicitly.
    for (const mapLayer of [this.terrain, this.weather, this.boundaries]) {
      if (!mapLayer.getVisible()) continue;
      const layer = this.map.getViewport()!.querySelector<HTMLElement>(`.${mapLayer.getClassName()}`);
      if (layer) drawLayerCanvas(ctx, layer, size);
    }
    ctx.fillStyle = '#fff'; ctx.fillRect(0, size[1], canvas.width, footerHeight);
    ctx.fillStyle = '#1c303c'; ctx.font = 'bold 16px sans-serif';
    let y = size[1] + 26;
    for (const line of title) {ctx.fillText(line, 16, y); y += 22;}
    ctx.font = '12px sans-serif';
    if (this.weather.getVisible()) {
      const colors = this.bundle.weather_style.colors, width = Math.min(360, size[0] - 32);
      colors.forEach((color, i) => {ctx.fillStyle = `rgb(${rgb(color).join(',')})`; ctx.fillRect(16 + i * width / colors.length, y, width / colors.length + 1, 12);});
      ctx.fillStyle = '#33424c';
      for (const tick of [10, 20, 30, 40, 50, 60, 70, 75]) {
        ctx.textAlign = tick === 10 ? 'left' : tick === 75 ? 'right' : 'center';
        ctx.fillText(String(tick), 16 + (tick - 10) / 65 * width, y + 27);
      }
      ctx.textAlign = 'left'; y += legendHeight;
    }
    ctx.fillStyle = '#33424c';
    for (const line of notes) {ctx.fillText(line, 16, y); y += 17;}
    return canvas.toDataURL('image/png');
  }
  dispose() { ++this.revision; this.gate.cancel(); this.map.setTarget(undefined); this.map.dispose(); this.sources.forEach(s => s.dispose()); }
}
