export type Frame = {url: string; valid_time: string; lead_seconds: number};
export type SourceInfo = {url: string; attribution: string; disclaimer: string};
export type BoundaryTiles = {origin: number[]; tile_size: number; resolutions: number[];
  keys: string[]; symbol_types: Record<string, string>};
export type Bundle = {
  viewer_bundle_version: number; mock: boolean; is_real_forecast: boolean;
  title: string; notice: string; variable: string; units: string; init_time: string;
  frames: Frame[]; extent: number[]; map_extent?: number[]; shape: number[]; native_shape: number[];
  terrain: SourceInfo & {colors: [number, string][]; shape?: number[]; land_only?: boolean};
  boundaries: SourceInfo & {tiles?: BoundaryTiles;
    styles: Record<string, {edgecolor: string; linewidth: number; linestyle?: string}>};
  weather_style: {min: number; max: number; step: number; colors: number[][]; over: number[]};
};
export type LayerName = 'weather' | 'terrain' | 'boundaries';
export type LayerSetting = {visible: boolean; opacity: number};
