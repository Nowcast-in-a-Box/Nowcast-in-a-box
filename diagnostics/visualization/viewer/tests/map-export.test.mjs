import test from 'node:test';
import assert from 'node:assert/strict';
import {compositeLayers, drawLayerCanvas} from '../src/map-export.ts';

function context() {
  return {draws:[], transforms:[], save(){}, restore(){},
    scale(x,y){this.transforms.push([x,0,0,y,0,0]);},
    setTransform(...matrix){this.transforms.push(matrix);},
    drawImage(canvas){this.draws.push({canvas,alpha:this.globalAlpha});}};
}
const canvas = () => ({tagName:'CANVAS',width:1200,height:800,
  style:{opacity:'',transform:''},querySelector:()=>null});

test('exports direct WebGL canvases without applying layer opacity twice', () => {
  // Observed OL DOM: <canvas class="weather-layer">, no child canvas.
  // Its pixel alpha already includes the configured layer opacity.
  const weather=canvas(), ctx=context();
  drawLayerCanvas(ctx,weather,[600,400]);
  assert.deepEqual(ctx.draws,[{canvas:weather,alpha:1}]);
  assert.deepEqual(ctx.transforms,[[.5,0,0,.5,0,0]]);
});

test('exports wrapped vector canvases using CSS opacity and pixel transform', () => {
  const boundary=canvas(), ctx=context();
  boundary.style.transform='matrix(0.5, 0, 0, 0.5, 12, 8)';
  const container={tagName:'DIV',style:{opacity:'.35'},
    querySelector:selector=>selector==='canvas'?boundary:null};
  drawLayerCanvas(ctx,container,[600,400]);
  assert.deepEqual(ctx.draws,[{canvas:boundary,alpha:.35}]);
  assert.deepEqual(ctx.transforms,[[.5,0,0,.5,12,8]]);
});

const exportScene = () => {
  const terrain=canvas(), presented=canvas(), preparing=canvas(), boundaries=canvas();
  const elements={'.terrain-layer':terrain,'.weather-buffer-a':preparing,
    '.weather-buffer-b':presented,'.boundary-layer':boundaries};
  const ctx=context();
  const find=cls=>elements[cls]??null;
  return {ctx,find,presented,preparing,terrain,boundaries};
};

test('export composites exactly the presented weather buffer, never the preparing one', () => {
  const scene=exportScene();
  compositeLayers(scene.ctx,[
    {selector:'.terrain-layer',visible:true},
    {selector:'.weather-buffer-b',visible:true},
    {selector:'.boundary-layer',visible:true},
  ],scene.find,[600,400]);
  assert.deepEqual(scene.ctx.draws.map(d=>d.canvas),[scene.terrain,scene.presented,scene.boundaries]);
});

test('export with weather hidden omits both weather buffers', () => {
  const scene=exportScene();
  compositeLayers(scene.ctx,[
    {selector:'.terrain-layer',visible:true},
    {selector:'.weather-buffer-b',visible:false},
    {selector:'.boundary-layer',visible:true},
  ],scene.find,[600,400]);
  assert.deepEqual(scene.ctx.draws.map(d=>d.canvas),[scene.terrain,scene.boundaries]);
});
