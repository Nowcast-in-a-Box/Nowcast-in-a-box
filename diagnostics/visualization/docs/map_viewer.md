# 本地交互地图

本页的独立演示入口仍可打开已准备的 mock bundle。真实预报由 diagnostics 写入每个 attempt 的 `viewer.json` 与 `weather/frame-NNNN.tif`。共享全球底图只准备一次，发布到 WMO cache 的 `viewer/`，不复制进每个 run。`basemap_sources/` 里的路径是来源记录，不是脚本默认值。

## 准备共享底图

需要 Python 3.12+、已安装的可视化依赖（含 rasterio）以及本机已校验的 WMO cache 和 ETOPO 2022 v1 / 30s / surface 全球 NetCDF。脚本不下载、不读取同事 data 目录，缺任一显式参数即失败。

从仓库根目录执行，把参数换成实际文件；`--basemap-output` 必须是 cache 内的 `viewer` 目录：

```bash
PYTHONPATH=diagnostics/visualization/src \
  python diagnostics/visualization/scripts/prepare_map_viewer.py \
  --wmo-cache <wmo-cache> \
  --terrain <etopo-netcdf> \
  --terrain-source-record diagnostics/visualization/basemap_sources/etopo2022-source.json \
  --terrain-sha256 <64-lowercase-hex> \
  --basemap-output <wmo-cache>/viewer
```

成功后该目录包含 `basemap.json`、`terrain-land.tif` 和 `wmo/0/0/0.pbf`。`basemap.json` 的 `terrain.url` 与 `boundaries.url` 是 cache 内相对路径；运行时再改写为 `/map-basemap/`。版本 4 的源 checksum、掩膜 manifest hash 和宽度参与缓存身份，命中则复用。缺失或校验失败会要求先准备全球底图，不会发布半成品。

地形按 WMO 陆地面掩膜，海洋透明。柔和自然色由 `src/nib_visualization/viewer_basemap.py::LAND_TERRAIN_COLORS` 定义。默认宽度 7200，对应约 3 arc-minute 显示格距，不是源 30 arc-second。天气 COG 由每次诊断运行生成，不由本脚本生成，也不再提供 mock 输入或 `--basemap-only`。

## 已有数据升级

掩膜缓存版本为 4。旧版派生地形需用上面的显式命令重新准备。只改源码或刷新浏览器不会重写地形文件。

## 真实运行结果

Handler 的统一 Diagnostics 页面直接嵌入相同的 React/OpenLayers viewer，不用
iframe，也不启动第二个服务。真实 bundle 标记 `mock:false`、
`is_real_forecast:true`，显示真实模型、变量、单位、初始化/有效时间和来源说明。
`diagnostics/map_visualization.py` 从 StoredFields 读取预测；NPZ 与评分数组不变，
COG 仅供地图显示。共享底图挂载为只读 `/basemap`，天气 COG 由 run 目录提供。
缺共享底图只阻止 visualization，metrics-only 仍可执行。

诊断镜像在 Node 构建阶段运行 `npm run build:embed`，生成
`viewer/dist-embed/visualization.js`；最终 Python 镜像不安装 Node。
同源 `/map-viewer/`、`/map-basemap/`、`/visualization/` 端点经过 Handler
结果代理，支持 COG byte ranges。独立演示入口仅用于已有已准备的 mock bundle。

## 日常启动与停止

以下独立演示命令在 `diagnostics/visualization/` 目录执行。

```bash
npm --prefix viewer start
npm --prefix viewer run status
npm --prefix viewer stop
```

启动先检查 manifest 引用的文件，执行生产构建并在后台提供 http://127.0.0.1:4173/ 。重复启动复用已有服务。日志在 `output/viewer-runtime/viewer-4173.log`；电脑重启后需再次启动，没有开机自启。停止命令仅处理身份匹配的本项目服务，端口被其他程序占用时不会替用户关闭它。

修改源码或重新生成数据后，先停止再启动。运行时读取 `viewer/dist/client/data/` 构建快照。可用同一 shell 环境变量 `NIB_VIEWER_PORT` 为启动、查询和停止指定端口；不自动加载 `.env`，不要在多个端口同时重建同一个 dist。开发热更新时先停生产入口，再运行：

```bash
npm --prefix viewer run dev -- --host 127.0.0.1 --port 4173 --strictPort
```

## 交互与验证

默认定位天气区域，可切到全球、平移与缩放；天气、地形和边界独立开关并设置不透明度。时间轴支持逐帧、播放和速度调整。0.5×/1×/2×/4×的目标换帧间隔为400/200/100/50ms，1×对应5fps；倍率只改变观看节奏，不改变科学时次。下一帧的准备计入间隔，就绪后到点替换完整天气帧，慢帧就绪后顺延，不跳帧追赶。暂停撤销在途自动帧并保留当前显示帧；已发起的手动选帧仍可完成，暂停或改倍率不会把它改成自动请求。正常切帧不弹加载提示，持续等待600ms后显示缓冲状态。播放期间导出按钮保持禁用，暂停且当前帧完成后可导出含时间、图层与来源说明的PNG。

```bash
npm --prefix viewer test
npm --prefix viewer run typecheck
npm --prefix viewer run build
npm --prefix viewer run test:sites
.venv/bin/python -m pytest ../../tests/visualization/test_viewer_bundle.py ../../tests/visualization/test_viewer_basemap.py -q
```

验证真实WMO解码、瓦片缓冲裁剪和地形掩膜时，显式启用本地缓存：

```bash
NIB_WMO_BASEMAP_PATH=<wmo-cache> .venv/bin/python -m pytest ../../tests/visualization/test_wmo_basemap.py ../../tests/visualization/test_viewer_basemap.py -q
```

接收环境没有WMO cache时，真实PBF解码测试会跳过；仅单元测试通过不能代替实际底图和浏览器验证。

2026-10-23地形空洞修复自验：相关WMO/掩膜/Core/render/COG测试58 passed、2项未启用OCHA缓存的既有集成测试跳过，Ruff、前端类型检查和生产构建通过。7200×3600全球地形与修复前逐格对比，恢复6,247格点，新增误删0，原有效高程不变；50天气文件hash不变。实际浏览器检查原缺口、另两处同类缺口、海岸和全球视图，主缺口周围260×280像素与实际下载PNG逐像素一致。此为绑定本地数据和环境的实施者验证，不是接收环境或新独立验收。

2026-10-22本地英文/底图版本：57项前端测试、4项Sites、类型与构建已有通过证据；本次提交前陆地掩膜和COG定向Python检查7 passed。真实Chrome已检查桌面1440×900、窄屏390×844、来源/图层/播放与实际PNG下载；PNG文字使用英文并优先按单词换行。新增底图和英文界面为实施者自验，未取得新的独立功能verdict；既有favicon 404保留，窄屏检查不等于完整移动布局验收。

2026-10-21播放修复验收（历史快照）：53项前端测试、类型检查与构建通过；4项Sites检查按未变范围复用。真实浏览器已覆盖完整帧切换、慢帧、取消、重试、倍率切换及实际PNG；普通1×/2×播放后，稳定暂停画面的天气、时间和帧号与同帧静态参照逐像素一致。

O-01保留为用户接受的deferred观察项：旧异步合成截图曾出现新天气与旧时间标签，但缺少该图自身呈现身份，无法判断是否实际显示；后续正常样本不能证明旧图是捕获伪影。当前按本地mock MVP范围完成使用验收，原天气/时间一致性要求保持。若稳定暂停仍错配、实际导出错时或正常播放出现可重复可见错配，再重新处理。

2026-10-12旧版本本机基线（历史记录）：9项前端测试、4项Sites测试、类型与构建通过；真实浏览器三层显示、播放回环和实际PNG内容已检查。COG的1024字节Range请求返回206。此前同一地图源码/数据的本机生产构建完整首帧可见146–209ms、4×单帧工作P95为34.8ms（Chromium、1280×720、DPR2、50帧1000×677 mock）；这些是本机基线，不是线上SLA或接收方设备测量。

这是本地预览。Sites兼容构建会生成 client/server/hosting 文件，但未执行发布；构建会复制本地数据，公开分发前仍需核验第三方来源许可。统一 Diagnostics 的真实 Forecast、镜像和 Handler 路径通过 runtime 验收；其他操作系统的实机启动需单独检查。
