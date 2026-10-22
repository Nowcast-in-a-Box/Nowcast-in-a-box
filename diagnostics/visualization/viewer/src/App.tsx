import {useEffect, useRef, useState, type CSSProperties} from 'react';
import {ArrowLineLeft, ArrowLineRight, CaretDown, CaretLeft, CaretRight, DownloadSimple, Info, Minus, Pause, Play, Plus, CornersOut, X, CloudSun, Mountains, GlobeHemisphereEast, SlidersHorizontal, Database} from '@phosphor-icons/react';
import {MapController, rgb} from './map';
import {nextFrame, PlaybackPacer} from './playback';
import type {Bundle, LayerName, LayerSetting} from './types';

const layerRows: {name: LayerName; title: string; subtitle: string; icon: typeof CloudSun}[] = [
  {name:'weather',title:'Weather',subtitle:'Radar reflectivity · dBZ',icon:CloudSun},
  {name:'terrain',title:'Terrain',subtitle:'ETOPO',icon:Mountains},
  {name:'boundaries',title:'Boundaries',subtitle:'WMO',icon:GlobeHemisphereEast},
];
const time = (iso:string) => iso.replace('T',' ').replace(':00Z',' UTC').replace('Z',' UTC');
const shortTime = (iso:string) => iso.slice(11,16);
const rangeStyle = (value:number,min=0,max=100) => ({'--progress':`${(value-min)/Math.max(1,max-min)*100}%`} as CSSProperties);

export function App() {
  const [bundle,setBundle]=useState<Bundle>();
  const [fatal,setFatal]=useState('');
  useEffect(()=>{
    const abort=new AbortController();
    fetch('/data/viewer.json',{signal:abort.signal}).then(r=>{
      if(!r.ok) throw new Error('The map data bundle is not ready.');
      return r.json();
    }).then((b:Bundle)=>{
      if(b.viewer_bundle_version!==1 || !b.frames?.length || !b.mock || b.is_real_forecast)
        throw new Error('This viewer only supports CMA bundles marked as observation replay.');
      setBundle(b);
    }).catch(e=>{if(e.name!=='AbortError')setFatal(e.message)});
    return ()=>abort.abort();
  },[]);
  if(!bundle)return <main className="startup"><strong>NiB Visualization</strong><p role="status">{fatal||'Opening the local map…'}</p>{fatal&&<button onClick={()=>location.reload()}>Reload</button>}</main>;
  return <Workspace bundle={bundle}/>;
}

function Workspace({bundle}:{bundle:Bundle}) {
  const target=useRef<HTMLDivElement>(null);
  const controller=useRef<MapController|undefined>(undefined);
  const legend=useRef<HTMLCanvasElement>(null);
  const download=useRef<HTMLAnchorElement>(null);
  const [ready,setReady]=useState(false);
  const [layers,setLayers]=useState<Record<LayerName,LayerSetting>>({
    weather:{visible:true,opacity:.96},terrain:{visible:true,opacity:.56},boundaries:{visible:true,opacity:1},
  });
  const [collapsed,setCollapsed]=useState(false);
  const [selected,setSelected]=useState(0);
  const [displayed,setDisplayed]=useState(0);
  const [loading,setLoading]=useState(true);
  const [slowLoading,setSlowLoading]=useState(false);
  const [playing,setPlaying]=useState(false);
  const [speed,setSpeed]=useState(1);
  const [threshold,setThreshold]=useState(10);
  const [showSources,setShowSources]=useState(false);
  const [error,setError]=useState('');
  const [retry,setRetry]=useState(0);
  const [beat,setBeat]=useState(0);
  // The auto-playback beat: commits anchor the next frame's deadline, and
  // start/resume/speed switches restart it from "now" instead of catching up.
  const pacer=useRef(new PlaybackPacer());
  // Marks the selection the playback advance is about to start as automatic;
  // read and cleared once by the selection effect.
  const autoAdvance=useRef(false);
  // Origin of the selection currently in flight, kept for its whole
  // lifecycle: pausing may only cancel automatic frames, never an explicit
  // manual choice. Cleared when that selection commits or fails.
  const pendingOrigin=useRef<'automatic'|'manual'|null>(null);
  const frame=bundle.frames[displayed];

  useEffect(()=>{
    if(!loading){setSlowLoading(false);return;}
    const timer=setTimeout(()=>setSlowLoading(true),600);
    return ()=>clearTimeout(timer);
  },[loading]);

  useEffect(()=>{
    const c=new MapController(target.current!,bundle,message=>{setError(message);setPlaying(false)});
    controller.current=c;setReady(true);
    const observer=new ResizeObserver(()=>c.resize());observer.observe(target.current!);
    return ()=>{observer.disconnect();c.dispose();controller.current=undefined};
  },[bundle]);
  useEffect(()=>{for(const row of layerRows)controller.current?.setLayer(row.name,layers[row.name])},[layers,ready]);
  useEffect(()=>{controller.current?.setThreshold(threshold)},[threshold,ready]);
  useEffect(()=>{
    if(!ready)return;
    // Re-selecting the frame already on screen is a no-op — unless an error
    // needs recovering from: the explicit retry (or a late failure of the
    // displayed frame's own tiles) must re-enter the real selection path.
    if(selected===displayed&&!loading&&!error)return;
    const auto=autoAdvance.current;autoAdvance.current=false;
    pendingOrigin.current=auto?'automatic':'manual';
    let active=true;setLoading(true);setError('');
    // Auto frames carry the pacer's deadline; manual and initial selections
    // commit as soon as their preparation is complete. `speed` is only read
    // for auto selections, which always re-run this effect with a fresh
    // closure (the advance or the speed switch triggers it in the same flush).
    void controller.current!.select(selected,()=>{
      if(active){pendingOrigin.current=null;pacer.current.noteCommit();setDisplayed(selected);setLoading(false)}
    },{delayMs:auto?pacer.current.commitDelay(speed):undefined})
      .catch(e=>{if(active){pendingOrigin.current=null;setError(e.message);setPlaying(false)}});
    return ()=>{active=false};
  },[selected,ready,retry,beat]);
  useEffect(()=>{
    if(!playing||loading||error||bundle.frames.length<2)return;
    // Advance at once: preparation of the next frame begins immediately and
    // its commit is held to the deadline computed from the last actual commit
    // (preparation time counts inside the target interval).
    const next=nextFrame(displayed,bundle.frames.length);
    if(selected===next)return;   // that frame's auto selection is already in flight
    autoAdvance.current=true;
    setSelected(next);
  },[playing,loading,displayed,selected,speed,error,bundle.frames.length]);
  useEffect(()=>{
    const onKey=(event:KeyboardEvent)=>{
      if(showSources||(event.target instanceof HTMLElement&&/INPUT|SELECT|BUTTON/.test(event.target.tagName)))return;
      if(event.code==='Space'){event.preventDefault();togglePlay()}
      if(event.code==='ArrowRight'||event.code==='ArrowLeft'){
        event.preventDefault();autoAdvance.current=false;setPlaying(false);
        setSelected(i=>Math.max(0,Math.min(bundle.frames.length-1,i+(event.code==='ArrowRight'?1:-1))));
      }
    };
    window.addEventListener('keydown',onKey);return ()=>window.removeEventListener('keydown',onKey);
  });
  useEffect(()=>{
    const canvas=legend.current!,ctx=canvas.getContext('2d')!,colors=bundle.weather_style.colors;
    colors.forEach((c,i)=>{ctx.fillStyle='rgb('+rgb(c).join(',')+')';ctx.fillRect(i*canvas.width/colors.length,0,canvas.width/colors.length+1,canvas.height)});
  },[bundle]);
  const updateLayer=(name:LayerName,change:Partial<LayerSetting>)=>setLayers(p=>({...p,[name]:{...p[name],...change}}));
  const togglePlay=()=>{
    if(!playing){pacer.current.restart();setPlaying(true);return}
    // Pausing invalidates the pending AUTOMATIC frame in both stages — still
    // preparing, or fully ready and waiting for its deadline. An explicit
    // manual selection is never cancelled: it keeps its selected/loading and
    // may still commit while paused. The displayed frame stays as-is either way.
    if(loading&&pendingOrigin.current==='automatic'){
      controller.current?.cancelPending();
      pendingOrigin.current=null;
      setLoading(false);
      setSelected(displayed);
    }
    setPlaying(false);
  };
  const changeSpeed=(value:number)=>{
    setSpeed(value);
    if(!playing)return;
    // A speed switch keeps exactly one beat: the pending auto frame is
    // re-issued with a deadline anchored at the switch, chasing neither the
    // old interval nor deadlines from before the switch. A pending MANUAL
    // selection is never re-issued: it keeps its request and origin (no
    // deadline, nothing superseded), and the new speed takes over from that
    // frame's actual commit — noteCommit anchors the next beat, and the
    // restart above already dropped pre-switch deadlines.
    pacer.current.restart();
    if(loading&&pendingOrigin.current==='automatic'){autoAdvance.current=true;setBeat(b=>b+1)}
  };
  const jump=(index:number)=>{autoAdvance.current=false;setPlaying(false);setSelected(index)};
  const ticks=Array.from(new Set([0,...[.2,.4,.6,.8,1].map(f=>Math.round(f*(bundle.frames.length-1)))]));

  return <main className={'workspace '+(collapsed?'sidebar-collapsed':'')}>
    <header className="toolbar">
      <div className="brand"><strong>NiB</strong><span>Visualization</span></div>
      <h1>{bundle.title}</h1><span className="mock-badge">{bundle.notice}</span>
      <button className="export" disabled={playing||loading||Boolean(error)} title={playing?'Pause playback to export the current view':undefined} onClick={()=>{const link=download.current!;link.href=controller.current!.exportPng(displayed);link.click()}}><DownloadSimple size={20}/>Export view</button>
      <a ref={download} hidden download={`nib-map-${displayed.toString().padStart(4,'0')}.png`}>Download map PNG</a>
    </header>
    <aside className="sidebar" aria-label="Map settings">
      <div className="sidebar-heading"><h2>Layers</h2><button className="icon-button" aria-label={collapsed?'Expand layer panel':'Collapse layer panel'} title={collapsed?'Expand layer panel':'Collapse layer panel'} onClick={()=>setCollapsed(v=>!v)}>{collapsed?<CaretRight/>:<CaretLeft/>}</button></div>
      <div className="sidebar-content">
        {layerRows.map(({name,title,subtitle,icon:LayerIcon})=><section className={'layer-row '+(layers[name].visible?'':'layer-off')} key={name}>
          <div className="layer-title"><h3><LayerIcon size={17} aria-hidden="true"/>{title}</h3><button className="switch" role="switch" aria-label={title+' layer'} aria-checked={layers[name].visible} onClick={()=>updateLayer(name,{visible:!layers[name].visible})}><span/></button></div>
          <p className="layer-subtitle">{subtitle}</p>
          <label className="opacity"><span>Opacity</span><input aria-label={title+' opacity'} type="range" min="0" max="100" value={Math.round(layers[name].opacity*100)} style={rangeStyle(layers[name].opacity*100)} disabled={!layers[name].visible} onChange={e=>updateLayer(name,{opacity:Number(e.target.value)/100})}/><output>{Math.round(layers[name].opacity*100)}%</output></label>
        </section>)}
        <p className="layer-help"><Info size={18}/>Toggle each layer independently</p>
        <div className="sidebar-bottom">
          <details className="color-settings"><summary><span><SlidersHorizontal size={16} aria-hidden="true"/>Color scale</span><CaretDown/></summary><p>Fixed at 10–75 dBZ throughout playback.</p><label>Display threshold <output>{threshold} dBZ</output><input aria-label="Display threshold" type="range" min="10" max="50" step=".5" value={threshold} style={rangeStyle(threshold,10,50)} onChange={e=>setThreshold(Number(e.target.value))}/></label><button className="text-button" onClick={()=>setThreshold(10)}>Reset color scale</button></details>
          <button className="sources-button" onClick={()=>setShowSources(true)}><span><Database size={16} aria-hidden="true"/>Data & sources</span><CaretRight/></button>
        </div>
      </div>
    </aside>
    <section className="map-column" aria-label="Weather map and time controls">
      <div className="map-shell">
        <div className="map" ref={target} aria-label="Weather map: drag to pan, scroll to zoom" tabIndex={0}/>
        <div className="map-controls"><div className="zoom-group"><button aria-label="Zoom in" title="Zoom in" onClick={()=>controller.current?.zoom(1)}><Plus/></button><button aria-label="Zoom out" title="Zoom out" onClick={()=>controller.current?.zoom(-1)}><Minus/></button></div><button className="fit-button" aria-label="Fit to data extent" title="Fit to data extent" onClick={()=>controller.current?.fit()}><CornersOut/><span>Data</span></button><button className="fit-button" aria-label="View global extent" title="View global extent" onClick={()=>controller.current?.fitWorld()}><GlobeHemisphereEast/><span>World</span></button></div>
        {error&&<div className="map-status error" role="alert">{error}<button onClick={()=>setRetry(n=>n+1)}>Retry</button></div>}
        <div className={'legend '+(layers.weather.visible?'':'hidden-weather')}><strong>Reflectivity <span className="legend-unit">dBZ</span>{!layers.weather.visible&&<span> · Hidden</span>}</strong><canvas width="520" height="28" ref={legend} aria-label="Reflectivity color scale, 10 to 75 dBZ"/><div className="legend-ticks">{[10,20,30,40,50,60,75].map(v=><span key={v} style={{left:`${(v-10)/65*100}%`}}>{v}</span>)}</div></div>
        <button className="attribution" onClick={()=>setShowSources(true)}>WMO · NOAA/NCEI ETOPO <span>| Sources & notes</span></button>
      </div>
      <footer className="timeline" aria-label="Time controls">
        <div className="time-heading"><strong><span className="time-label">Current time</span> <time dateTime={frame.valid_time}>{time(frame.valid_time)}</time></strong><span className="lead">+{(frame.lead_seconds/60).toString().padStart(3,'0')} min · Replay offset</span><span className="frame-count">{displayed+1} / {bundle.frames.length} frames</span></div>
        <div className="playback-row"><div className="playback-buttons"><button className="step" aria-label="Previous frame" disabled={selected===0} onClick={()=>jump(selected-1)}><ArrowLineLeft size={20}/></button><button className="play-button" aria-label={playing?'Pause':'Play'} disabled={bundle.frames.length<2||Boolean(error)} onClick={togglePlay}>{playing?<Pause weight="fill"/>:<Play weight="fill"/>}{playing?'Pause':'Play'}</button><button className="step" aria-label="Next frame" disabled={selected===bundle.frames.length-1} onClick={()=>jump(selected+1)}><ArrowLineRight size={20}/></button></div>
          <div className="scrubber"><input type="range" aria-label="Timeline" aria-valuetext={time(bundle.frames[selected].valid_time)} min="0" max={bundle.frames.length-1} value={selected} style={rangeStyle(selected,0,bundle.frames.length-1)} onChange={e=>jump(Number(e.target.value))}/><div className="time-ticks">{ticks.map(i=><span key={i} style={{left:`${i/Math.max(1,bundle.frames.length-1)*100}%`}}>{shortTime(bundle.frames[i].valid_time)}</span>)}</div></div>
          <select aria-label="Playback speed" value={speed} onChange={e=>changeSpeed(Number(e.target.value))}>{[.5,1,2,4].map(s=><option key={s} value={s}>{s}×</option>)}</select>
        </div>
        <div className="time-caption"><span>Replay reference&nbsp; {time(bundle.init_time)}<span className="loading-hint" role="status">{loading&&slowLoading&&!error?'Buffering…':''}</span></span><span>Display grid {bundle.shape[1]} × {bundle.shape[0]}<span className="keyboard-hint"> · Space to play/pause, ← → to step</span></span></div>
      </footer>
    </section>
    {showSources&&<Sources bundle={bundle} onClose={()=>setShowSources(false)}/>}
  </main>;
}
function Sources({bundle,onClose}:{bundle:Bundle;onClose:()=>void}){
  const dialog=useRef<HTMLDialogElement>(null);
  useEffect(()=>{dialog.current?.showModal()},[]);
  return <dialog ref={dialog} className="source-dialog" onCancel={onClose} onClick={e=>{if(e.target===dialog.current)onClose()}}>
    <div className="dialog-title"><h2>Data & sources</h2><button className="icon-button" aria-label="Close source notes" onClick={onClose}><X/></button></div>
    <p className="source-notice">{bundle.notice}. Historical CMA radar observations are replayed in time order; these are not model forecasts.</p>
    <dl><dt>Variable & unit</dt><dd>Radar reflectivity · dBZ</dd><dt>Time range</dt><dd>{time(bundle.frames[0].valid_time)} — {shortTime(bundle.frames.at(-1)!.valid_time)} UTC, {bundle.frames.length} frames</dd><dt>Display grid / native grid</dt><dd>{bundle.shape[1]} × {bundle.shape[0]} / {bundle.native_shape[1]} × {bundle.native_shape[0]}. Spatial sampling uses the nearest original grid points; all time steps are retained. Zooming does not increase the source resolution. Overviews at smaller scales are for display only.</dd><dt>Value notes</dt><dd>Echoes below the current display threshold are transparent; this is not quality control. Special source codes have not been decoded. Playback speed only changes the viewing rate.</dd></dl>
    <h3>WMO boundaries</h3><p>{bundle.boundaries.attribution}</p><p>{bundle.boundaries.disclaimer}</p><p>{bundle.boundaries.tiles ? 'Local boundary detail levels are selected by zoom; areas without finer detail use available parent tiles. Place-name labels are not included.' : 'Global boundaries use a local overview with fixed geometry; zooming does not add source detail. Place-name labels are not included.'}</p>
    <h3>ETOPO terrain</h3><p>{bundle.terrain.attribution}</p><p>{bundle.terrain.disclaimer}</p><p>The terrain source is 30 arc-second; this viewer uses a derived global grid{bundle.terrain.shape ? ` (${bundle.terrain.shape[1]} × ${bundle.terrain.shape[0]})` : ""} with multiple overview levels. {bundle.terrain.land_only ? 'Only land terrain is shown; the ocean is transparent over a uniform background. Land below sea level is retained. ' : ''}Weather data cover only the observed region.</p>
    <button className="dialog-done" onClick={onClose}>Done</button>
  </dialog>;
}
