/** Compose an OpenLayers layer element in CSS pixels, preserving rendered alpha. */
export function drawLayerCanvas(ctx: CanvasRenderingContext2D, element: HTMLElement, size: number[]) {
  const canvas = element.tagName === 'CANVAS'
    ? element as HTMLCanvasElement : element.querySelector<HTMLCanvasElement>('canvas');
  if (!canvas?.width) return;
  ctx.save();
  // WebGL bakes layer opacity into pixels; vector layers use a CSS container.
  const opacity = element.style.opacity || canvas.style.opacity;
  ctx.globalAlpha = opacity === '' ? 1 : Number(opacity);
  const transform = canvas.style.transform.match(/^matrix\(([^)]+)\)$/);
  if (transform) ctx.setTransform(...transform[1].split(',').map(Number) as [number, number, number, number, number, number]);
  else ctx.scale(size[0] / canvas.width, size[1] / canvas.height);
  ctx.drawImage(canvas, 0, 0);
  ctx.restore();
}

/**
 * Composite the layers the user currently sees, in map order. Callers pass
 * exactly one weather layer — the presented buffer — so a frame preparing on
 * the hidden buffer is never composited alongside the displayed one.
 */
export function compositeLayers(ctx: CanvasRenderingContext2D,
  layers: {selector: string; visible: boolean}[],
  findElement: (selector: string) => HTMLElement | null, size: number[]) {
  for (const layer of layers) {
    if (!layer.visible) continue;
    const element = findElement(layer.selector);
    if (element) drawLayerCanvas(ctx, element, size);
  }
}
