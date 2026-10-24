# 本地交互地图

本页命令从 `diagnostics/visualization/` 模块目录执行。该页面是可独立运行的本地展示入口，尚未接入 diagnostics 统一入口或真实 Forecast reader。

## 准备依赖与数据

需要 Python 3.12+、Node.js 和 npm（本轮使用 Node.js 26.0.0；测试命令依赖 Node 直接加载 TypeScript 的能力）。使用已有 Core 安装环境，安装可选 COG 依赖及前端依赖：

```bash
.venv/bin/python -m pip install -r requirements-viewer.lock.txt
.venv/bin/python -m pip install --no-deps -e .
npm --prefix viewer ci
```

按[数据准备指南](data_setup.md)准备 WMO cache、完整 ETOPO 2022 v1 / 30s / surface 全球 NetCDF，并使用组员已有的 CMA CREF NPZ 序列。地图使用全球 ETOPO 原件，不依赖区域地形裁剪产物。原始数据均留在 gitignored 的 data/，本仓库不附带数据。

当前准备脚本沿用演示模块的旧色表路径，先将已随包交付的 REF 表放到该本地路径；该复制文件不入库：

```bash
mkdir -p 'data/02_雷达拼图读取_看输入输出，绘制回波图'
cp resources/cma_ref_colormap.json 'data/02_雷达拼图读取_看输入输出，绘制回波图/color_map_v202408.json'
.venv/bin/python scripts/prepare_map_viewer.py --input data/cma-npz
```

将 `data/cma-npz` 换成实际 NPZ 目录。准备脚本批量生成天气COG、全球陆地地形COG、WMO本地矢量瓦片及 `output/map-viewer/data/viewer.json`，逐格回读天气显示网格后写入 manifest。默认天气显示宽度1000、全球地形宽度7200；缩放不能恢复原生网格细节。WMO按缩放读取本地LOD 0–9；没有更细实体瓦片的区域回退到实际父级几何，不拉伸到子瓦片。它不包含街道或地名标签，继续放大不保证增加细节。当前50帧场景是实况回放，不能标为真实预报。

地形按WMO陆地面掩膜，海洋透明露出统一浅蓝底色，低于海平面的陆地仍保留。默认柔和自然色由`src/nib_visualization/viewer_basemap.py::LAND_TERRAIN_COLORS`定义，不透明度56%；当前7200×3600派生网格为3 arc-minute显示格距，不是源30 arc-second或30米DEM。

界面、来源说明、加载/错误、tooltips、无障碍标签和PNG注释统一英文。生成标题为`CMA CREF · Radar reflectivity`，标记为`Observation replay · Not a forecast`；`Data`和`World`分别恢复数据区及全球视野。

只更新底图可用 `--basemap-only`；抽查首中末三帧可用 `--sample --output output/map-viewer-sample/data`，该独立目录不会替换默认页面数据。

## 已有数据升级：地形空洞修复

WMO瓦片现在先完整解码，再按真实地理瓦片范围裁剪，避免内置裁剪丢失部分陆地面。地形掩膜缓存版本已由3升为4；已有版本3的派生地形需重新准备，并重新构建本地预览：

```bash
.venv/bin/python scripts/prepare_map_viewer.py --basemap-only
npm --prefix viewer stop
npm --prefix viewer start
```

命令仍需已有的全球ETOPO原件和WMO缓存，来源及下载步骤不变。`--basemap-only`会重建旧版陆地COG，并逐文件核对天气帧hash保持一致。仅更新源码或刷新浏览器不会重写旧地形文件；重启会把新COG复制到运行时构建快照。

该修复保留地形显示格距、陆地负高程、柔和自然色、图层不透明度和天气播放行为；不增加地形分辨率或边界源精度。

## 日常启动与停止

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
.venv/bin/python -m pytest tests/test_viewer_bundle.py tests/test_viewer_basemap.py -q
```

验证真实WMO解码、瓦片缓冲裁剪和地形掩膜时，显式启用本地缓存：

```bash
NIB_WMO_BASEMAP_PATH=data/cache/basemap/wmo .venv/bin/python -m pytest tests/test_wmo_basemap.py tests/test_viewer_basemap.py -q
```

接收环境没有WMO cache时，真实PBF解码测试会跳过；仅单元测试通过不能代替实际底图和浏览器验证。

2026-10-23地形空洞修复自验：相关WMO/掩膜/Core/render/COG测试58 passed、2项未启用OCHA缓存的既有集成测试跳过，Ruff、前端类型检查和生产构建通过。7200×3600全球地形与修复前逐格对比，恢复6,247格点，新增误删0，原有效高程不变；50天气文件hash不变。实际浏览器检查原缺口、另两处同类缺口、海岸和全球视图，主缺口周围260×280像素与实际下载PNG逐像素一致。此为绑定本地数据和环境的实施者验证，不是接收环境或新独立验收。

2026-10-22本地英文/底图版本：57项前端测试、4项Sites、类型与构建已有通过证据；本次提交前陆地掩膜和COG定向Python检查7 passed。真实Chrome已检查桌面1440×900、窄屏390×844、来源/图层/播放与实际PNG下载；PNG文字使用英文并优先按单词换行。新增底图和英文界面为实施者自验，未取得新的独立功能verdict；既有favicon 404保留，窄屏检查不等于完整移动布局验收。

2026-10-21播放修复验收（历史快照）：53项前端测试、类型检查与构建通过；4项Sites检查按未变范围复用。真实浏览器已覆盖完整帧切换、慢帧、取消、重试、倍率切换及实际PNG；普通1×/2×播放后，稳定暂停画面的天气、时间和帧号与同帧静态参照逐像素一致。

O-01保留为用户接受的deferred观察项：旧异步合成截图曾出现新天气与旧时间标签，但缺少该图自身呈现身份，无法判断是否实际显示；后续正常样本不能证明旧图是捕获伪影。当前按本地mock MVP范围完成使用验收，原天气/时间一致性要求保持。若稳定暂停仍错配、实际导出错时或正常播放出现可重复可见错配，再重新处理。

2026-10-12旧版本本机基线（历史记录）：9项前端测试、4项Sites测试、类型与构建通过；真实浏览器三层显示、播放回环和实际PNG内容已检查。COG的1024字节Range请求返回206。此前同一地图源码/数据的本机生产构建完整首帧可见146–209ms、4×单帧工作P95为34.8ms（Chromium、1280×720、DPR2、50帧1000×677 mock）；这些是本机基线，不是线上SLA或接收方设备测量。

这是本地预览。Sites兼容构建会生成 client/server/hosting 文件，但未执行发布；构建会复制本地数据，公开分发前仍需核验第三方来源许可。真实Forecast接入、统一镜像与跨平台运行另行验收。
