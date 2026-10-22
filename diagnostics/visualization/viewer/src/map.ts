import Map from 'ol/Map.js';
import {createMapView, WORLD_EXTENT} from './map-view';
import GeoTIFF from 'ol/source/GeoTIFF.js';
import WebGLTileLayer from 'ol/layer/WebGLTile.js';
import {createBoundaryLayer} from './boundary-layer.ts';
import {ScaleLine} from 'ol/control.js';
import {unByKey} from 'ol/Observable.js';
import type {ExpressionValue} from 'ol/expr/expression.js';
import type {EventsKey} from 'ol/events.js';
import type {Bundle, LayerName, LayerSetting} from './types';
import {awaitStableRender, SelectionGate} from './playback';
import {compositeLayers} from './map-export';

const url = (path: string) => `/data/${path}`;
export const rgb = (c: number[]) => c.map(v => Math.round(v * 255));

export class MapController {
  readonly map: Map;
  readonly terrain: WebGLTileLayer;
  readonly boundaries: ReturnType<typeof createBoundaryLayer>;
  /** Two weather buffers: one presents the displayed frame, the other prepares the next frame hidden. */
  readonly weatherLayers: [WebGLTileLayer, WebGLTileLayer];
  private sources = new globalThis.Map<number, GeoTIFF>();
  private gate = new SelectionGate();
  private revision = 0;
  private onError: (message: string) => void;
  private bundle: Bundle;
  private threshold = 10;
  private presented = 0;
  private pendingSource: GeoTIFF | null = null;
  /** Every live selection's timeout/failure hooks; dispose() ends them all. */
  private pendingWaits = new Set<{timer: number; fail: (error: Error) => void}>();
  private latestIndex = 0;
  private disposed = false;

  constructor(target: HTMLElement, bundle: Bundle, onError: (message: string) => void) {
    this.bundle = bundle;
    this.onError = onError;
    // CSS presents exactly one buffer (see styles.css): the other keeps
    // rendering fully while hidden, so the swap is one atomic style change.
    target.dataset.weatherBuffer = 'a';
    this.weatherLayers = [
      new WebGLTileLayer({className: 'weather-buffer-a', opacity: .96,
        style: this.weatherStyle(10), cacheSize: 128}),
      new WebGLTileLayer({className: 'weather-buffer-b', opacity: .96,
        style: this.weatherStyle(10), cacheSize: 128}),
    ];
    const terrainSource = this.source(bundle.terrain.url, 'terrain');
    this.terrain = new WebGLTileLayer({className: 'terrain-layer', opacity: .56,
      source: terrainSource, style: {color: [
        'case', ['==', ['band', 2], 0], [0, 0, 0, 0],
        ['interpolate', ['linear'], ['band', 1], ...bundle.terrain.colors.flat()],
      ] as ExpressionValue}});
    this.boundaries = createBoundaryLayer(bundle.boundaries, onError);
    this.map = new Map({target, layers: [this.terrain, ...this.weatherLayers, this.boundaries],
      controls: [new ScaleLine({units: 'metric', minWidth: 100})],
      view: createMapView(bundle.map_extent)});
    this.fit();
  }

  private source(path: string, role: 'terrain' | 'weather') {
    const source = new GeoTIFF({sources: [{url: url(path)}], normalize: false,
      interpolate: false, transition: 0, wrapX: false});
    source.on('tileloaderror', () => {
      // Terrain is a persistent shared basemap: any tile failure is
      // user-visible. Weather frames are only reported while being prepared
      // or presented; superseded in-flight requests must stay silent.
      if (role === 'terrain' || source === this.pendingSource || source === this.presentedWeather().getSource())
        this.onError('Failed to load map data. The current view is not fully loaded.');
    });
    return source;
  }

  private getSource(index: number) {
    let source = this.sources.get(index);
    if (!source) { source = this.source(this.bundle.frames[index].url, 'weather'); this.sources.set(index, source); }
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

  private presentedWeather() { return this.weatherLayers[this.presented]; }
  private preparingWeather() { return this.weatherLayers[1 - this.presented]; }
  private present(layer: WebGLTileLayer) {
    this.presented = this.weatherLayers.indexOf(layer);
    this.map.getTargetElement()!.dataset.weatherBuffer = this.presented === 0 ? 'a' : 'b';
  }
  private viewSignature() {
    const view = this.map.getView(), size = this.map.getSize() ?? [0, 0];
    return `${view.getCenter()}|${view.getResolution()}|${view.getRotation()}|${size}`;
  }
  /** What a finished preparation was finished *for*: the view plus the
   * weather presentation settings. A change here after readiness means the
   * early ready result no longer describes what would be committed. */
  private presentationSignature() {
    const weather = this.weatherLayers[0];
    return `${this.viewSignature()}|t${this.threshold}|${weather.getVisible()}@${weather.getOpacity()}`;
  }

  setThreshold(value: number) {
    this.threshold = value;
    for (const layer of this.weatherLayers) layer.setStyle(this.weatherStyle(value));
  }
  setLayer(name: LayerName, settings: LayerSetting) {
    if (name === 'weather')
      for (const layer of this.weatherLayers) { layer.setVisible(settings.visible); layer.setOpacity(settings.opacity); }
    else { this[name].setVisible(settings.visible); this[name].setOpacity(settings.opacity); }
  }
  fit() { this.map.getView().fit(this.bundle.extent, {padding: [24, 32, 40, 32]}); }
  fitWorld() { this.map.getView().fit(this.bundle.map_extent ?? WORLD_EXTENT, {padding: [20, 20, 20, 20]}); }
  zoom(delta: number) { const view = this.map.getView(); view.animate({zoom: (view.getZoom() ?? 4) + delta, duration: 180}); }
  resize() { this.map.updateSize(); }

  /** Invalidate the pending selection without committing it (pause). The
   * gate blocks its commit; the revision bump settles an already-armed render
   * wait at the next completion event instead of re-arming, so the card-01
   * stale-wait boundary is preserved. A late completion or failure of the
   * cancelled selection stays silent — including through the permanent source
   * error handler, whose pending-source ownership is dropped here. The
   * presented frame's source keeps its own reporting, and a later
   * re-selection of the same source re-establishes ownership at its own
   * select() start. */
  cancelPending() {
    ++this.revision;
    this.gate.cancel();
    this.pendingSource = null;
  }

  async select(index: number, commit: () => void, opts?: {delayMs?: number}) {
    const revision = ++this.revision;
    this.latestIndex = index;
    return this.gate.run(async () => {
      // The deadline anchors at this call and keeps running while the frame
      // prepares, so preparation time counts inside the target interval.
      let clearDeadline = () => {};
      const deadline = opts?.delayMs !== undefined
        ? new Promise<void>(resolve => {
          const id = window.setTimeout(resolve, opts.delayMs);
          clearDeadline = () => window.clearTimeout(id);
        })
        : null;
      const source = this.getSource(index);
      this.pendingSource = source;
      // Failure hooks are closure-local: a superseded selection's cleanup must
      // never disconnect the current selection's timer or rejection path.
      let failWait: (error: Error) => void = () => {};
      const failure = new Promise<never>((_, reject) => { failWait = reject; });
      const errorKey = source.once('tileloaderror', () => failWait(new Error('Failed to load the weather frame')));
      const timer = window.setTimeout(() => failWait(new Error('Map loading timed out. Please retry.')), 30000);
      // Every live selection registers its hooks so dispose() can end all of
      // them, not only the latest one.
      const live = {timer, fail: failWait};
      this.pendingWaits.add(live);
      // All render listeners installed below are revocable from one place, so
      // failure, timeout, staleness or dispose cannot leave one armed.
      const revokeRenderListeners: Array<() => void> = [];
      try {
        // The GeoTIFF header read can hang forever on a failed fetch, so it
        // stays inside the same failure/timeout race as the render wait.
        await Promise.race([source.getView(), failure]);
        if (revision !== this.revision) return;
        const prep = this.preparingWeather();
        prep.setSource(source);
        // OpenLayers computes its completion flag during a render and
        // dispatches rendercomplete as a 0ms task, so an event computed for
        // the previous frame can still arrive after this source swap; by
        // itself it proves nothing about the new source. Readiness is bound
        // to the preparation itself: only a completion from a frame in which
        // the preparing layer actually drew this source may commit. A hidden
        // weather layer is not rendered at all, so for it the map-level
        // completion (which ignores invisible layers) is authoritative.
        let drewPreparation = false;
        const prepKey = prep.on('postrender', () => {
          if (prep.getSource() === source) drewPreparation = true;
        });
        revokeRenderListeners.push(() => unByKey(prepKey));
        const nextRender = () => new Promise<void>(resolve => {
          // A superseded selection ends its wait instead of re-arming: an
          // abandoned wait must not keep registering listeners and requesting
          // renders of an already idle map. Its armed listener always has a
          // completion in flight (arm pairs the listener with a render
          // request), so the wait terminates on that event and select()'s
          // unified cleanup releases its hooks.
          const complete = () => {
            if (revision !== this.revision || drewPreparation || !prep.getVisible()) resolve();
            else arm();
          };
          const arm = () => {
            const key = this.map.once('rendercomplete', complete);
            revokeRenderListeners.push(() => unByKey(key));
            this.map.render();
          };
          // Re-entry after a view change must not put a stale selection back
          // into waiting either — it settles without touching the map.
          if (revision !== this.revision) resolve();
          else arm();
        });
        // Ready means every visible layer finished rendering for the view that
        // is still current; view changes during the wait re-arm the wait and
        // the selection's own failure ends it instead of suspending it.
        await awaitStableRender(() => this.viewSignature(), nextRender, failure);
        if (revision !== this.revision) return;
        // A paced (auto) frame holds its complete preparation until the
        // deadline. Readiness is not frozen while waiting: if the view or the
        // layer settings changed during the wait, the ready result is stale
        // and the frame re-validates at the current conditions before it may
        // commit; a genuine failure during the wait still fails the selection.
        if (deadline) {
          const settled = this.presentationSignature();
          await Promise.race([deadline, failure]);
          if (revision !== this.revision) return;
          if (this.presentationSignature() !== settled)
            await awaitStableRender(() => this.presentationSignature(), nextRender, failure);
          if (revision !== this.revision) return;
        }
      } catch (error) {
        // A failed selection must not leave a poisoned source cached or
        // attached to the hidden preparation layer: drop it and release it
        // (unless the presented frame still shows it) so a retry builds fresh.
        if (revision === this.revision) this.discardFailed(index, source);
        throw error;
      } finally {
        clearDeadline();
        for (const revoke of revokeRenderListeners) revoke();
        window.clearTimeout(timer);
        unByKey(errorKey);
        this.pendingWaits.delete(live);
        // Only the still-current selection may drop the pending-source error
        // ownership: a superseded or cancelled selection's cleanup must never
        // strip it from a newer request that may hold the same source object.
        if (revision === this.revision && this.pendingSource === source) this.pendingSource = null;
        // Bounded source cache on every selection end — success, failure,
        // timeout or stale — so failed requests cannot accumulate sources.
        this.evictSources();
      }
    }, () => {
      // Commit swaps the fully rendered preparation into view in one DOM
      // change, then reports the frame as actually displayed.
      this.present(this.preparingWeather());
      commit();
      this.evictSources();
      const next = (index + 1) % this.bundle.frames.length;
      void this.getSource(next).getView().catch(() => {});
    });
  }

  /** Drop a failed selection's source: out of the cache, off the hidden
   * preparation layer, and disposed — unless the presented frame still shows
   * it (retrying the displayed frame shares that source). */
  private discardFailed(index: number, source: GeoTIFF) {
    this.sources.delete(index);
    const prep = this.preparingWeather();
    if (prep.getSource() === source) prep.setSource(null);
    if (this.presentedWeather().getSource() !== source) source.dispose();
  }

  /**
   * Bounded source cache: after every selection end, keep the ±2
   * neighbourhood (which includes the wrap-around prefetch target of the
   * anchor) of the latest request plus whatever the presented and hidden
   * preparation layers still hold; release everything else. That is at most
   * five cached indices plus the two layer-held sources.
   */
  private evictSources() {
    if (this.disposed) return;
    const count = this.bundle.frames.length;
    const keep = new Set([-2, -1, 0, 1, 2].map(offset => (this.latestIndex + offset + count) % count));
    const retained = [this.presentedWeather().getSource(), this.preparingWeather().getSource(), this.pendingSource];
    for (const [key, source] of this.sources) {
      if (keep.has(key) || retained.includes(source)) continue;
      this.sources.delete(key);
      source.dispose();
    }
  }

  exportPng(index: number) {
    this.map.renderSync();
    const size = this.map.getSize()!;
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d')!;
    const frame = this.bundle.frames[index];
    const wrap = (text: string, width: number) => {
      const lines: string[] = []; let line = '';
      for (const word of text.split(/\s+/)) {
        if (line && ctx.measureText(`${line} ${word}`).width > width) {lines.push(line); line = '';}
        if (line) line += ' ';
        // Preserve words where possible; split only tokens wider than a line,
        // such as a source URL in a narrow export.
        for (const char of word) {
          if (line && ctx.measureText(line + char).width > width) {lines.push(line); line = '';}
          line += char;
        }
      }
      if (line) lines.push(line);
      return lines;
    };
    const weather = this.presentedWeather();
    ctx.font = 'bold 16px sans-serif';
    const title = wrap(`${this.bundle.title} | ${frame.valid_time} | ${this.bundle.notice}`, size[0] - 32);
    ctx.font = '12px sans-serif';
    const layerNote = `Layers: weather ${weather.getVisible() ? 'on' : 'off'} / terrain ${this.terrain.getVisible() ? 'on' : 'off'} / boundaries ${this.boundaries.getVisible() ? 'on' : 'off'}; display threshold ${this.threshold} dBZ.`;
    const notes = wrap(`${layerNote} Display grid ${this.bundle.shape[1]} × ${this.bundle.shape[0]}; current view screenshot. ${this.bundle.boundaries.attribution} ${this.bundle.terrain.attribution} ${this.bundle.boundaries.disclaimer}`, size[0] - 32);
    const legendHeight = weather.getVisible() ? 48 : 0;
    const footerHeight = 30 + title.length * 22 + legendHeight + notes.length * 17;
    canvas.width = size[0]; canvas.height = size[1] + footerHeight;
    ctx.fillStyle = getComputedStyle(this.map.getTargetElement()!.parentElement!).backgroundColor;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    // Hidden OpenLayers canvases may retain old pixels: compose only the
    // presented weather buffer plus the other visible layers.
    compositeLayers(ctx, [
      {selector: `.${this.terrain.getClassName()}`, visible: this.terrain.getVisible()},
      {selector: `.${weather.getClassName()}`, visible: weather.getVisible()},
      {selector: `.${this.boundaries.getClassName()}`, visible: this.boundaries.getVisible()},
    ], selector => this.map.getViewport()!.querySelector<HTMLElement>(selector), size);
    ctx.fillStyle = '#fff'; ctx.fillRect(0, size[1], canvas.width, footerHeight);
    ctx.fillStyle = '#1c303c'; ctx.font = 'bold 16px sans-serif';
    let y = size[1] + 26;
    for (const line of title) {ctx.fillText(line, 16, y); y += 22;}
    ctx.font = '12px sans-serif';
    if (weather.getVisible()) {
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
  dispose() {
    this.disposed = true;
    ++this.revision; this.gate.cancel();
    // End every live selection, not only the latest one: stale gate results
    // are already swallowed, so their late failures surface nowhere.
    for (const live of this.pendingWaits) { window.clearTimeout(live.timer); live.fail(new Error('The view has been closed')); }
    this.pendingWaits.clear();
    this.pendingSource = null;
    this.map.setTarget(undefined); this.map.dispose(); this.sources.forEach(s => s.dispose());
  }
}
