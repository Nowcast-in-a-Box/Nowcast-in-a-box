import {createRoot} from 'react-dom/client';
import {App} from './App.tsx';
import olCss from 'ol/ol.css?inline';
import viewerCss from './styles.css?inline';

const embeddedCss = `${olCss}\n${viewerCss}`;


/**
 * Mount the map inside `host` and return a disposer.
 * The diagnostics page calls this as `mountVisualization(host, bundleUrl)` and
 * must call the returned function on sample, model, or tab removal.
 * Disposal unmounts React (aborting the bundle fetch, disconnecting the map
 * ResizeObserver, and running MapController.dispose) and then clears the shadow
 * tree. A second call is a no-op. The host element itself is left in place.
 */
export function mountVisualization(host: HTMLElement, bundleUrl: string): () => void {
  if (!(host instanceof HTMLElement))
    throw new Error('A map host element and bundle URL are required.');
  const shadow = host.shadowRoot ?? host.attachShadow({mode: 'open'});
  shadow.replaceChildren();
  const style = document.createElement('style');
  style.textContent = embeddedCss;
  const mountPoint = document.createElement('div');
  mountPoint.className = 'nib-map-embed';
  shadow.append(style, mountPoint);
  const root = createRoot(mountPoint);
  root.render(<App bundleUrl={bundleUrl} />);
  let disposed = false;
  return () => {
    if (disposed) return;
    disposed = true;
    root.unmount();
    shadow.replaceChildren();
  };
}
