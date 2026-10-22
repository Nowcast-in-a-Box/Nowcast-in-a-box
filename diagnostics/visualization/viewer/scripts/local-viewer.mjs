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
  console.log(`Running (production build): ${url}\nPID: ${info.pid}; frames: ${info.frames}; started: ${info.startedAt}\nLog: ${log}`);
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
  if (existing === 'other') throw new Error(`Port ${port} is in use by another service. Stop the existing dev server or set NIB_VIEWER_PORT. Other processes will not be stopped automatically.`);
  await ensurePortFree();
  const bundle = await inspectViewerData(resolve(root, '../output/map-viewer/data'));
  console.log(`Data check passed (${bundle.frames.length} frames). Building the map…`);
  execFileSync('npm', ['run', 'build'], {cwd:root, stdio:'inherit'});
  await inspectViewerData(resolve(root, 'dist/client/data'));
  await mkdir(runtime, {recursive:true});
  const fd = openSync(log, 'a');
  const child = spawn(process.execPath, [entry, 'serve'], {
    cwd:root, detached:true, stdio:['ignore', fd, fd, 'ipc'], env:process.env,
  });
  try {
    const info = await new Promise((done, fail) => {
      const timer = setTimeout(() => { child.kill('SIGTERM'); fail(new Error(`Startup timed out. See ${log}`)); }, 15000);
      child.once('message', message => { clearTimeout(timer); message.error ? fail(new Error(message.error)) : done(message); });
      child.once('error', error => { clearTimeout(timer); fail(error); });
      child.once('exit', code => { clearTimeout(timer); fail(new Error(`Preview server exited (${code}). See ${log}`)); });
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
  console.log(`${info.startedAt} Production preview ${url}`);
  process.send?.(info);
}

async function stop() {
  const info = await probe();
  if (!info || info === 'other') { console.log('No production preview for this project is running. Other services were left unchanged.'); return; }
  process.kill(info.pid, 'SIGTERM');
  for (let i = 0; i < 30; i++) {
    if (!(await probe())) { console.log(`Stopped: ${url}`); return; }
    await new Promise(done => setTimeout(done, 100));
  }
  throw new Error('Stop requested, but the server has not exited yet. Run npm run status shortly.');
}

try {
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('NIB_VIEWER_PORT must be an integer port number from 1 to 65535.');
  switch (process.argv[2] ?? 'start') {
    case 'start': await start(); break;
    case 'serve': await serve(); break;
    case 'stop': await stop(); break;
    case 'status': {
      const info = await probe();
      if (info && info !== 'other') describe(info);
      else console.log(info === 'other' ? `${url} is used by another service, not this project’s production preview.` : `This project’s production preview is not running: ${url}`);
      break;
    }
    default: throw new Error('Usage: npm start | npm stop | npm run status');
  }
} catch (error) {
  console.error(`Map service: ${error.message}`);
  process.send?.({error:error.message});
  process.exitCode = 1;
}
