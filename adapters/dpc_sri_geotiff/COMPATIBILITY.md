# IRENE / ConvGRU 数据适配判断

## 结论先行

当前 IRENE 镜像可**直接**接收的只有完整的意大利 DPC SRI 雷达产品：连续 6 帧、
5 分钟间隔、`RR` 雨强（mm/h）、DPC 原始 `1200×1400` 网格、GeoTIFF 或原版支持的
NetCDF。它不是“任意雷达数组都能运行”的通用容器。

## 已检查的本地 MLCast DPC 样例

位置：

```text
C:\dataloader\nib-data-samples\mlcast\it_dpc_sri_5min\sample.zarr
```

它与 IRENE 的数据**同源、同变量语义、同时间间隔**：

| 项目 | MLCast 样例 | IRENE 当前输入 |
| --- | --- | --- |
| 产品 | Italy DPC SRI | Italy DPC SRI |
| 变量 / 单位 | `RR` / mm/h | `RR` / mm/h |
| 时间间隔 | 5 min | 5 min |
| 文件格式 | Zarr | 完整 GeoTIFF（也支持原版 NetCDF） |
| 帧数 | 5 | 连续 6 |
| 空间范围 | 已裁为 `256×256`，样例原点 `[572, 472]` | 先读完整 `1200×1400`，默认裁 `256×256`，窗口原点约 `[400, 394]` |

所以它**不是同一种数据的简单格式差异**：当前样例少一帧，而且裁剪位置与 IRENE 默认计算区域不同。原版 IRENE decoder 会先验证完整 DPC 原生网格，因此不能直接把该 Zarr 或其 256×256 数组塞进当前镜像。

要使用 MLCast DPC archive，需另做一个明确的新工作：扩展/替换 IRENE 输入 decoder，使其能读取 Zarr，并让模型配置使用该样例对应空间窗口；之后还要验证预训练权重在这个区域与历史数据上的科学有效性。它不应被当作“无损格式转换”处理。

## 已有 NiB Data Adapter 与当前 IRENE 的关系

| 数据 Adapter | 与 IRENE 的关系 | 原因 |
| --- | --- | --- |
| `dpc_sri_geotiff` | **直接可用** | 本次新增；完整 DPC SRI GeoTIFF、6 帧、5 min、RR/mm/h、原生网格。|
| `mlcast_it_dpc_sri_5min` | **最接近，但当前不可直连** | 同产品/变量/频率；当前本地样例是 5 帧、256×256 Zarr，且空间窗口不同。|
| `cma_radar` | **不可直连** | CMA CREF 反射率 dBZ，6 min，中国网格；IRENE 权重和 decoder 均不是此数据。适配 pySTEPS LK，而不是 IRENE。|
| `mlcast_opera_plus_it_vmi` | **不可直连** | VMI 反射率 dBZ；项目中已有对应 pySTEPS LK 产品。|
| `mlcast_dmi_10_minutes` | **不可直连** | dBZ、10 min、丹麦网格。|
| `mlcast_radklim_5_minutes` / `hourly` | **不可直连** | 降水量 mm，不是雨强 mm/h；德国网格，且 hourly 时间间隔也不同。|
| `mlcast_uk_metoffice_5min` / `mlcast_be_rmi_radclim_mfb_5min` / `mlcast_fr_mf_prate_5min` | **变量和频率较接近，但不可直接使用** | 均为 5 min 雨强类产品，但网格、观测处理和国家区域不同；不能假设意大利预训练权重可迁移。|
| `fy4b` / `gk2a` / `himawari9` / `mtg` / `sevir` | **不可直连** | 卫星或 VIL 等非 DPC 雷达雨强产品，物理变量、观测几何和时间频率不同。|

## 推荐的下一步

1. **先维持当前边界**：DPC SRI GeoTIFF → IRENE 是唯一直接、已验证的 ConvGRU 产品链。
2. **CMA 与 OPERA VMI**：继续使用已接入的 pySTEPS LK；它是数据无关的光流基线，但仍须由各 Adapter 负责变量和网格处理。
3. **若要探索 MLCast DPC archive**：先补一个至少 6 帧、覆盖 IRENE 默认窗口的 archive 样例；随后单独设计 `mlcast-zarr-to-irene-input` Adapter。不要修改当前稳定的 `dpc_sri_geotiff` 链路。
4. **若要让 ConvGRU 支持其他国家**：这不是 Container 格式转换问题，而是模型适用性问题；需要模型作者确认、独立评估，必要时重新训练或微调。
