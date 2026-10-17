# 检验数据接口：单 run 的 xarray 标准

接口标准版本：2。实现：`diagnostics.run_source.RunSource`。

四个业务方法沿用检验同事提供的接口形状。本版明确原生投影网格、辅助经纬度及变量身份，替代“所有数据必须是 EPSG:4326 一维经纬度”的限制。读取时不重采样、不运行模型、不计算指标。

首批支持保存为 `nib.run.v1` 的 **CMA Radar** run；SEVIR 的新接口适配暂缓，现有 provider 不变。支持范围是显式边界，网格、模型名、单位、路径和时间均从 run 读取，不固定为演示样例。

## 1. 初始化与组配置

在 diagnostics Python 环境或镜像内：

```python
from diagnostics.run_source import RunSource, RunSourceError

source = RunSource(
    "/run",
    version=1,
    group_id="my-run-verification",  # 可省略，默认为 <run-id>-verification
    models=["nowcastnet"],          # 可省略，选择所有已保存的预报
    metrics=[
        {"family": "continuous", "items": ["mae", "rmse"]},
        {"family": "yes_or_no", "items": ["ts"], "levels": [10, 20, 30]},
    ],
)
```

- 平台负责选择 run 并传入冻结的指标配置。消费者不需要知道内部 NPZ 文件结构。
- 一个对象绑定一个 run、一份组配置及一个或多个已保存的模型。没有跨 run 扫描或汇总。
- `version` 是必填的正整数。`group_id + version` 表示同一份检验语义；改变网格、变量、单位、时效/模型选择或指标参数时，由平台增加版本，或创建新的组 ID。
- 读取器是只读快照，不是跨进程的配置注册库，**不会自动维护或检查历史版本递增**。调用者不能复用同一身份表示不同配置。
- 初始化会验证观测和选中模型的 checksum、网格、变量、单位和时效，缓存各 cube。返回对象可以修改，不影响后续读取和源文件。

## 2. 四个方法

```python
source.list_groups() -> list[str]
source.get_group(group_id: str) -> dict
source.read_truth(name: str, time: str) -> xarray.DataArray
source.read_forecast(name: str, init_time: str, lead_minutes: int) -> xarray.DataArray
```

`list_groups()` 返回本对象的一项组 ID；`get_group()` 返回独立副本。不存在的组、名称、时刻和时效均抛 `RunSourceError`，不返回 None 或空对象。

读取标识来自 `get_group()`，不要用裸数据集或模型 ID 猜名称：

```python
group = source.get_group(source.list_groups()[0])
truth_name = group["truth"]["name"]
forecast_name = group["forecasts"][0]["name"]

initial = group["time"]["init_from"]
lead = group["forecasts"][0]["leads_minutes"][0]
forecast = source.read_forecast(forecast_name, initial, lead)
truth = source.read_truth(truth_name, forecast.attrs["valid_time"])
```

### 组声明

```python
{
    "group_id": "my-run-verification",
    "name": "<sample-id>",
    "version": 1,
    "status": "active",
    "truth": {
        "name": "<run-id>/truth/<data-id>",
        "variable": "CREF",
        "unit": "dBZ",
        "cadence_minutes": 6,
    },
    "space": {
        "projection": "<真实 CRS，EPSG 标识或 WKT>",
        "coordinate_units": "m",
        "extent": [xmin, ymin, xmax, ymax],
        "resolution": [dx, dy],
    },
    "time": {
        "timezone": "UTC",
        "init_from": "<带时区起报时刻>",
        "init_to": "<同一个起报时刻>",
    },
    "forecasts": [{
        "name": "<run-id>/forecast/<model-id>",
        "model_id": "<model-id>",
        "display_name": "<可读名称>",
        "variable": "CREF",
        "unit": "dBZ",
        "leads_minutes": [6, 12, 18],  # 实际产出，不是固定长度
    }],
    "metrics": [{"family": "continuous", "items": ["mae", "rmse"]}],
}
```

`forecasts[].name` 是全局读取身份，`variable` 是科学变量，两者不能混用。`time.init_from/init_to` 是预报起报范围，不是实况可读范围；实况可读取 run 中保存的 context 和 target 时刻。

投影场使用 `extent`，单位与原生轴一致；EPSG:4326 地理场改用 `bbox: [西, 南, 东, 北]`，`coordinate_units: "degrees"`。范围是首尾格点中心，不是像元外边界；resolution 顺序为 `[x 步长, y 步长]`。同组观测和预报必须具有相同原生网格、变量和单位，不一致时拒绝，不静默对齐。

### 指标参数

| family | 必需参数 | 可选参数 |
|---|---|---|
| `yes_or_no` | `items`、`levels` | `unit` |
| `continuous` | `items` | `levels`、`unit` |
| `spatial` | `items`、`levels`、`half_windows` | `unit` |
| `moments` | `items` | `unit` |

`family` 封闭。具体指标归族由现有 `nib_verify` 注册表验证，例如 `corr` 属于 `moments`，不是 `continuous`；`tmmsss` 是中间统计量，不是可选最终指标。`ts` 与 `csi` 是同一类指标的名称约定，不应同时重复配置。

levels 为有限数值阈值，不是区间。单组不得重复，同一族的多组 levels 也不得重叠；half_windows 为非负整数，含 0 表示单格窗口。显式给 unit 时必须与实况相同。没有默认阈值或隐式单位换算。

## 3. DataArray 的空间与属性

### 原生投影场

```python
da.dims                         # ("y", "x")
da.coords["x"]                  # 一维原生 x，严格递增
da.coords["y"]                  # 一维原生 y，严格递增
da.coords["x"].attrs["units"]   # 例如 m
da.coords["lon"].dims           # ("y", "x")，辅助经度
da.coords["lat"].dims           # ("y", "x")，辅助纬度
da.coords["spatial_ref"].attrs["crs_wkt"]  # 真实 CRS
```

投影平面上的规则格点转换到经纬度后通常是曲线网格，不能准确压成两条一维 lon/lat。二维辅助坐标仅用于地理定位；生成它们不会更改数组、掩码或原生计算网格。指标计算不得把辅助经纬度当作已重采样的规则经纬度网格。

### EPSG:4326 地理场

```python
da.dims                         # ("lat", "lon")
da.coords["lon"].dims           # ("lon",)
da.coords["lat"].dims           # ("lat",)
```

两条轴一维且严格递增；坐标属性分别为 `degrees_east`、`degrees_north`。本接口不会将其他地理 CRS 静默标成 WGS84。

### 共同属性

```python
da.attrs["units"]        # 存储的真实单位，不假装换算成另一物理量
da.attrs["variable"]     # 对应 truth.variable / forecasts[].variable
da.attrs["valid_time"]   # UTC ISO 8601，含 Z
da.attrs["crs"]          # EPSG 或 WKT
da.attrs["grid_mapping"] # spatial_ref
da.attrs["source_name"]  # 对应请求的读取身份
```

时间不作为维度。`da.values` 是内存 numpy 数组，不使用 dask 惰性加载。投影 CF 参数和 WKT 位于标量 `spatial_ref` 坐标，x/y 具有投影轴的 standard_name，lon/lat 具有地理坐标的 standard_name。

## 4. 时间、缺测与异常

- 绝对时刻必须为带时区 ISO 8601 字符串；允许不同 UTC offset，归一到 UTC 精确匹配，不插值。
- lead_minutes 必须为整数分钟，不接受 bool、float 或字符串；只读取实际保存的时效。
- 部分缺测为 NaN，不填 0。整场没有有效格点时抛 `RunSourceError`。
- 配置错误、不存在的名称或时刻、损坏 checksum、网格不匹配及不支持的数据集使用统一 `RunSourceError`，保留可读原因和异常链。
- `source.file_reads` 报告已验证的 cube 加载次数。同一对象中每个选中 cube 只加载一次；修改返回的 DataArray 或组字典不会污染缓存。

## 5. 计算与存储的职责

提供对象不写数据库、不生成网页、不运行 ONNX。检验消费者负责计算、指标结果和网页内容，平台负责调度与统一结果外壳。

原接口草稿中的 `/run/products/diagnostics.sqlite` 是消费者希望使用的输出路径，不是本数据接口的副作用。本批不迁移当前按 attempt 保留的 `diagnostics/verification/<attempt>/verify.sqlite`；消费者接入时应单独对齐输出登记，不能覆盖历史报告。

完整可运行示例见 `diagnostics/examples/run_source_example.py`，通过模块运行：

```sh
python -m diagnostics.examples.run_source_example --run /run \
  --metrics /run/metrics-for-handoff.json --version 1
```

示例配置文件由平台准备，是一个指标组 JSON 列表；不要求它位于 run 内，也不由提供对象修改 run。
