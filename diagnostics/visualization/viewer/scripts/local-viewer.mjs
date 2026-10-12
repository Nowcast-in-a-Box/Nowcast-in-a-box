#!/usr/bin/env node
// Local production-build preview; intentionally uses Vite, not a new web server.
import {spawn, execFileSync} from 'node:child_process';
import {createServer} from 'node:net';
import {mkdir, stat} from 'node:fs/promises';
import {openSync, closeSync} from 'node:fs';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {inspectViewerData} from './viewer-data.mjs';

const entry = fileURLToPath(import.meta.url);
const root = resolve(dirname(entry), '..');
const host = '127.0.0.1';
const port = Number(process.env.NIB_VIEWER_PORT ?? 4173);
const identityPath = '/__nib_local_viewer';
const url = `http://${host}:${port}/`;
const runtime = resolve(root, '../output/viewer-runtime');
const log = resolve(runtime, `viewer-${port}.log`);
const app = 'nib-visualization-local';

async function probe() {
  try {
    const response = await fetch(new URL(identityPath, url), {signal:AbortSignal.timeout(1000)});
    const info = await response.json().catch(() => null);
    if (info?.app === app && info.root === root && info.mode === 'production' && Number.isSafeInteger(info.pid) && info.pid > 0) return info;
    return 'other';
  } catch { return null; }
}

function describe(info) {
  console.log(`运行中（生产构建）：${url}\nPID：${info.pid}；帧数：${info.frames}；启动：${info.startedAt}\n日志：${log}`);
}

async function ensurePortFree() {
  await new Promise((done, fail) => {
    const socket = createServer();
    socket.once('error', fail);
    socket.listen(port, host, () => socket.close(done));
  });
}

async function start() {
  const existing = await probe();
  if (existing && existing !== 'other') { describe(existing); return; }
  if (existing === 'other') throw new Error(`端口 ${port} 已由其他服务使用；先停止原开发服务，或设置 NIB_VIEWER_PORT。不会自动终止其他进程。`);
  await ensurePortFree();
  const bundle = await inspectViewerData(resolve(root, '../output/map-viewer/data'));
  console.log(`数据检查通过（${bundle.frames.length}帧），正在构建地图…`);
  execFileSync('npm', ['run', 'build'], {cwd:root, stdio:'inherit'});
  await inspectViewerData(resolve(root, 'dist/client/data'));
  await mkdir(runtime, {recursive:true});
  const fd = openSync(log, 'a');
  const child = spawn(process.execPath, [entry, 'serve'], {
    cwd:root, detached:true, stdio:['ignore', fd, fd, 'ipc'], env:process.env,
  });
  try {
    const info = await new Promise((done, fail) => {
      const timer = setTimeout(() => { child.kill('SIGTERM'); fail(new Error(`启动超时；请查看 ${log}`)); }, 15000);
      child.once('message', message => { clearTimeout(timer); message.error ? fail(new Error(message.error)) : done(message); });
      child.once('error', error => { clearTimeout(timer); fail(error); });
      child.once('exit', code => { clearTimeout(timer); fail(new Error(`预览服务退出（${code}）；请查看 ${log}`)); });
    });
    child.disconnect(); child.unref();
    describe(info);
  } finally { closeSync(fd); }
}

async function serve() {
  await stat(resolve(root, 'dist/client/index.html'));
  const bundle = await inspectViewerData(resolve(root, 'dist/client/data'));
  const info = {app, root, mode:'production', pid:process.pid, frames:bundle.frames.length, startedAt:new Date().toISOString()};
  const {preview} = await import('vite');
  const server = await preview({root, preview:{host, port, strictPort:true},
    plugins:[{name:'local-viewer-identity', configurePreviewServer(server) {
      server.middlewares.use((request, response, next) => {
        if (request.url !== identityPath) return next();
        response.setHeader('Content-Type', 'application/json');
        response.setHeader('Cache-Control', 'no-store');
        response.end(JSON.stringify(info));
      });
    }}],
  });
  const shutdown = () => server.httpServer.close(() => process.exit(0));
  process.once('SIGTERM', shutdown); process.once('SIGINT', shutdown);
  console.log(`${info.startedAt} 生产构建预览 ${url}`);
  process.send?.(info);
}

async function stop() {
  const info = await probe();
  if (!info || info === 'other') { console.log('未发现可停止的本项目生产预览；其他服务未改动。'); return; }
  process.kill(info.pid, 'SIGTERM');
  for (let i = 0; i < 30; i++) {
    if (!(await probe())) { console.log(`已停止：${url}`); return; }
    await new Promise(done => setTimeout(done, 100));
  }
  throw new Error('已发送停止请求，服务尚未退出；请稍后运行 npm run status。');
}

try {
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('NIB_VIEWER_PORT 必须是1–65535的端口号。');
  switch (process.argv[2] ?? 'start') {
    case 'start': await start(); break;
    case 'serve': await serve(); break;
    case 'stop': await stop(); break;
    case 'status': {
      const info = await probe();
      if (info && info !== 'other') describe(info);
      else console.log(info === 'other' ? `${url} 由其他服务使用，非本项目生产预览。` : `本项目生产预览未运行：${url}`);
      break;
    }
    default: throw new Error('用法：npm start | npm stop | npm run status');
  }
} catch (error) {
  console.error(`地图服务：${error.message}`);
  process.send?.({error:error.message});
  process.exitCode = 1;
}
