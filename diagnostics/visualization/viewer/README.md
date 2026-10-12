# NiB 地图页面

React + OpenLayers 本地交互地图，读取预生成的天气 COG、全球 ETOPO 地形和 WMO 矢量边界。

先按[使用与数据准备](../docs/map_viewer.md)准备依赖和本地数据，再在本目录运行：

```bash
npm start
npm run status
npm stop
```

默认地址为 http://127.0.0.1:4173/ 。源码或数据更新后先停止再启动。
依赖由 package-lock.json 固定；本地数据、node_modules、构建和运行产物不入库。
