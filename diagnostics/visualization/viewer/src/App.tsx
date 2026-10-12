import {useEffect, useRef, useState, type CSSProperties} from 'react';
import {ArrowLineLeft, ArrowLineRight, CaretDown, CaretLeft, CaretRight, DownloadSimple, Info, Minus, Pause, Play, Plus, CornersOut, X, CloudSun, Mountains, GlobeHemisphereEast, SlidersHorizontal, Database} from '@phosphor-icons/react';
import {MapController, rgb} from './map';
import {nextFrame} from './playback';
import type {Bundle, LayerName, LayerSetting} from './types';

const layerRows: {name: LayerName; title: string; subtitle: string; icon: typeof CloudSun}[] = [
  {name:'weather',title:'天气场',subtitle:'雷达反射率 · dBZ',icon:CloudSun},
  {name:'terrain',title:'地形',subtitle:'ETOPO',icon:Mountains},
  {name:'boundaries',title:'边界',subtitle:'WMO',icon:GlobeHemisphereEast},
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
      if(!r.ok) throw new Error('地图数据包尚未准备完成。');
      return r.json();
    }).then((b:Bundle)=>{
      if(b.viewer_bundle_version!==1 || !b.frames?.length || !b.mock || b.is_real_forecast)
        throw new Error('当前页面仅支持已标记的 CMA 实况回放数据包。');
      setBundle(b);
    }).catch(e=>{if(e.name!=='AbortError')setFatal(e.message)});
    return ()=>abort.abort();
  },[]);
  if(!bundle)return <main className="startup"><strong>NiB Visualization</strong><p role="status">{fatal||'正在打开本地地图…'}</p>{fatal&&<button onClick={()=>location.reload()}>重新加载</button>}</main>;
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
    let active=true;setLoading(true);setError('');
    void controller.current!.select(selected,()=>{if(active){setDisplayed(selected);setLoading(false)}})
      .catch(e=>{if(active){setError(e.message);setPlaying(false)}});
    return ()=>{active=false};
  },[selected,ready,retry]);
  useEffect(()=>{
    if(!playing||loading||error||bundle.frames.length<2)return;
    const timer=setTimeout(()=>setSelected(nextFrame(displayed,bundle.frames.length)),800/speed);
    return ()=>clearTimeout(timer);
  },[playing,loading,displayed,speed,error,bundle.frames.length]);
  useEffect(()=>{
    const onKey=(event:KeyboardEvent)=>{
      if(showSources||(event.target instanceof HTMLElement&&/INPUT|SELECT|BUTTON/.test(event.target.tagName)))return;
      if(event.code==='Space'){event.preventDefault();setPlaying(v=>!v)}
      if(event.code==='ArrowRight'||event.code==='ArrowLeft'){
        event.preventDefault();setPlaying(false);
        setSelected(i=>Math.max(0,Math.min(bundle.frames.length-1,i+(event.code==='ArrowRight'?1:-1))));
      }
    };
    window.addEventListener('keydown',onKey);return ()=>window.removeEventListener('keydown',onKey);
  },[showSources,bundle.frames.length]);
  useEffect(()=>{
    const canvas=legend.current!,ctx=canvas.getContext('2d')!,colors=bundle.weather_style.colors;
    colors.forEach((c,i)=>{ctx.fillStyle='rgb('+rgb(c).join(',')+')';ctx.fillRect(i*canvas.width/colors.length,0,canvas.width/colors.length+1,canvas.height)});
  },[bundle]);
  const updateLayer=(name:LayerName,change:Partial<LayerSetting>)=>setLayers(p=>({...p,[name]:{...p[name],...change}}));
  const jump=(index:number)=>{setPlaying(false);setSelected(index)};
  const ticks=Array.from(new Set([0,...[.2,.4,.6,.8,1].map(f=>Math.round(f*(bundle.frames.length-1)))]));

  return <main className={'workspace '+(collapsed?'sidebar-collapsed':'')}>
    <header className="toolbar">
      <div className="brand"><strong>NiB</strong><span>Visualization</span></div>
      <h1>{bundle.title}</h1><span className="mock-badge">{bundle.notice}</span>
      <button className="export" disabled={playing||loading||Boolean(error)} title={playing?'请先暂停播放，再导出当前视图':undefined} onClick={()=>{const link=download.current!;link.href=controller.current!.exportPng(displayed);link.click()}}><DownloadSimple size={20}/>导出当前视图</button>
      <a ref={download} hidden download={`nib-map-${displayed.toString().padStart(4,'0')}.png`}>下载地图 PNG</a>
    </header>
    <aside className="sidebar" aria-label="地图设置">
      <div className="sidebar-heading"><h2>图层</h2><button className="icon-button" aria-label={collapsed?'展开图层面板':'收起图层面板'} title={collapsed?'展开图层面板':'收起图层面板'} onClick={()=>setCollapsed(v=>!v)}>{collapsed?<CaretRight/>:<CaretLeft/>}</button></div>
      <div className="sidebar-content">
        {layerRows.map(({name,title,subtitle,icon:LayerIcon})=><section className={'layer-row '+(layers[name].visible?'':'layer-off')} key={name}>
          <div className="layer-title"><h3><LayerIcon size={17} aria-hidden="true"/>{title}</h3><button className="switch" role="switch" aria-label={title+'图层'} aria-checked={layers[name].visible} onClick={()=>updateLayer(name,{visible:!layers[name].visible})}><span/></button></div>
          <p className="layer-subtitle">{subtitle}</p>
          <label className="opacity"><span>不透明度</span><input aria-label={title+'不透明度'} type="range" min="0" max="100" value={Math.round(layers[name].opacity*100)} style={rangeStyle(layers[name].opacity*100)} disabled={!layers[name].visible} onChange={e=>updateLayer(name,{opacity:Number(e.target.value)/100})}/><output>{Math.round(layers[name].opacity*100)}%</output></label>
        </section>)}
        <p className="layer-help"><Info size={18}/>图层可独立关闭</p>
        <div className="sidebar-bottom">
          <details className="color-settings"><summary><span><SlidersHorizontal size={16} aria-hidden="true"/>色标设置</span><CaretDown/></summary><p>固定范围 10–75 dBZ，播放时保持一致。</p><label>最低显示值 <output>{threshold} dBZ</output><input aria-label="最低显示值" type="range" min="10" max="50" step=".5" value={threshold} style={rangeStyle(threshold,10,50)} onChange={e=>setThreshold(Number(e.target.value))}/></label><button className="text-button" onClick={()=>setThreshold(10)}>恢复默认色标</button></details>
          <button className="sources-button" onClick={()=>setShowSources(true)}><span><Database size={16} aria-hidden="true"/>数据与来源</span><CaretRight/></button>
        </div>
      </div>
    </aside>
    <section className="map-column" aria-label="天气地图与时间控制">
      <div className={'map-shell '+(loading||error?'frame-loading':'')}>
        <div className="map" ref={target} aria-label="天气地图：拖动平移，滚轮缩放" tabIndex={0}/>
        <div className="map-controls"><div className="zoom-group"><button aria-label="放大地图" title="放大地图" onClick={()=>controller.current?.zoom(1)}><Plus/></button><button aria-label="缩小地图" title="缩小地图" onClick={()=>controller.current?.zoom(-1)}><Minus/></button></div><button className="fit-button" aria-label="回到数据范围" onClick={()=>controller.current?.fit()}><CornersOut/><span>数据范围</span></button><button className="fit-button" aria-label="查看全球范围" onClick={()=>controller.current?.fitWorld()}><GlobeHemisphereEast/><span>全球范围</span></button></div>
        {error&&<div className="map-status error" role="alert">{error}<button onClick={()=>setRetry(n=>n+1)}>重试</button></div>}
        <div className={'legend '+(layers.weather.visible?'':'hidden-weather')}><strong>反射率 <span className="legend-unit">dBZ</span>{!layers.weather.visible&&<span> · 已隐藏</span>}</strong><canvas width="520" height="28" ref={legend} aria-label="反射率色标，10到75 dBZ"/><div className="legend-ticks">{[10,20,30,40,50,60,75].map(v=><span key={v} style={{left:`${(v-10)/65*100}%`}}>{v}</span>)}</div></div>
        <button className="attribution" onClick={()=>setShowSources(true)}>WMO · NOAA/NCEI ETOPO <span>| 来源与说明</span></button>
      </div>
      <footer className="timeline" aria-label="时间控制">
        <div className="time-heading"><strong><span className="time-label">当前时刻</span> <time dateTime={frame.valid_time}>{time(frame.valid_time)}</time></strong><span className="lead">+{(frame.lead_seconds/60).toString().padStart(3,'0')} min · 回放偏移</span><span className="frame-count">{displayed+1} / {bundle.frames.length} 帧</span></div>
        <div className="playback-row"><div className="playback-buttons"><button className="step" aria-label="上一帧" disabled={selected===0} onClick={()=>jump(selected-1)}><ArrowLineLeft size={20}/></button><button className="play-button" aria-label={playing?'暂停':'播放'} disabled={bundle.frames.length<2||Boolean(error)} onClick={()=>setPlaying(v=>!v)}>{playing?<Pause weight="fill"/>:<Play weight="fill"/>}{playing?'暂停':'播放'}</button><button className="step" aria-label="下一帧" disabled={selected===bundle.frames.length-1} onClick={()=>jump(selected+1)}><ArrowLineRight size={20}/></button></div>
          <div className="scrubber"><input type="range" aria-label="时间轴" aria-valuetext={time(bundle.frames[selected].valid_time)} min="0" max={bundle.frames.length-1} value={selected} style={rangeStyle(selected,0,bundle.frames.length-1)} onChange={e=>jump(Number(e.target.value))}/><div className="time-ticks">{ticks.map(i=><span key={i} style={{left:`${i/Math.max(1,bundle.frames.length-1)*100}%`}}>{shortTime(bundle.frames[i].valid_time)}</span>)}</div></div>
          <select aria-label="播放速度" value={speed} onChange={e=>setSpeed(Number(e.target.value))}>{[.5,1,2,4].map(s=><option key={s} value={s}>{s}×</option>)}</select>
        </div>
        <div className="time-caption"><span>回放基准&nbsp; {time(bundle.init_time)}<span className="loading-hint" role="status">{loading&&slowLoading&&!error?'正在缓冲…':''}</span></span><span>显示取样 {bundle.shape[1]} × {bundle.shape[0]}<span className="keyboard-hint"> · 空格播放，← → 逐帧</span></span></div>
      </footer>
    </section>
    {showSources&&<Sources bundle={bundle} onClose={()=>setShowSources(false)}/>}
  </main>;
}
function Sources({bundle,onClose}:{bundle:Bundle;onClose:()=>void}){
  const dialog=useRef<HTMLDialogElement>(null);
  useEffect(()=>{dialog.current?.showModal()},[]);
  return <dialog ref={dialog} className="source-dialog" onCancel={onClose} onClick={e=>{if(e.target===dialog.current)onClose()}}>
    <div className="dialog-title"><h2>数据与来源</h2><button className="icon-button" aria-label="关闭来源说明" onClick={onClose}><X/></button></div>
    <p className="source-notice">{bundle.notice}。将 CMA 历史雷达实况按时间顺序回放，不代表模型预报。</p>
    <dl><dt>变量与单位</dt><dd>雷达反射率 · dBZ</dd><dt>时间范围</dt><dd>{time(bundle.frames[0].valid_time)} — {shortTime(bundle.frames.at(-1)!.valid_time)} UTC，{bundle.frames.length} 帧</dd><dt>显示网格 / 原生网格</dt><dd>{bundle.shape[1]} × {bundle.shape[0]} / {bundle.native_shape[1]} × {bundle.native_shape[0]}。空间取最近原格点，时间未抽样；缩放不会提高原始分辨率。缩小时的概览仅用于显示。</dd><dt>数值说明</dt><dd>低于当前显示阈值的回波透明；这不是质量控制。源数据特殊码尚未解码。播放速度只改变观看速度。</dd></dl>
    <h3>WMO 边界</h3><p>{bundle.boundaries.attribution}</p><p>{bundle.boundaries.disclaimer}</p><p>全球边界使用本地概览数据，几何细节固定；缩放不会增加原始细节，不包含地名标签。</p>
    <h3>ETOPO 地形</h3><p>{bundle.terrain.attribution}</p><p>{bundle.terrain.disclaimer}</p><p>原始地形为 30 arc-second；页面使用全球派生网格{bundle.terrain.shape ? `（${bundle.terrain.shape[1]} × ${bundle.terrain.shape[0]}）` : ""}和多级概览，天气数据仅覆盖实际观测区域。</p>
    <button className="dialog-done" onClick={onClose}>知道了</button>
  </dialog>;
}
