# 检验接口说明

本文定义检验模块消费的 4 个接口。**实现在上游**，本文只定义形状。

---

## 1. `list_groups()`

```python
list_groups() -> list[str]
```

| | |
|---|---|
| 输入 | 无 |
| 返回 | `group_id` 字符串列表 |
| 错误 | — |

```python
["radar-csi-2025q3", "rain-fss-2025q3", "bt-me-2025q3"]
```

---

## 2. `get_group(group_id)`

```python
get_group(group_id: str) -> dict
```

| | |
|---|---|
| 输入 | `group_id`（来自 `list_groups()`） |
| 返回 | 字典，字段见下 |
| 错误 | `group_id` 不存在 → 抛异常 |

### 返回结构

```python
{
    # 身份
    "group_id":  "radar-csi-2025q3",
    "name":      "Radar reflectivity CSI, 2025 Q3",     # 展示用
    "version":   1,                                     # 语义版本
    "status":    "active",                              # active | retired

    # 实况
    "truth": {
        "name":            "radar-composite",           # 传给 read_truth 的名字
        "variable":        "reflectivity",
        "unit":            "dBZ",
        "cadence_minutes": 6,                           # 实况时间步长
    },

    # 空间
    "space": {
        "bbox":       [112.0, 34.0, 120.0, 42.0],       # [西, 南, 东, 北]
        "resolution": [0.1, 0.1],                       # 度
        "projection": "EPSG:4326",
    },

    # 时间
    "time": {
        "timezone":  "UTC",
        "init_from": "2025-07-01T00:00:00Z",
        "init_to":   "2025-09-30T23:00:00Z",
    },

    # 预报
    "forecasts": [
        {"name": "mlcast-det", "unit": "dBZ", "leads_minutes": [10, 20, 30, ..., 180]},
        {"name": "mlcast-alt", "unit": "dBZ", "leads_minutes": [10, 20, 30, ..., 180]},
    ],

    # 指标
    "metrics": [
        {"family": "yes_or_no",  "items": ["ts", "csi", "bias"],
         "levels": [20, 30, 40, 50, 60], "unit": "dBZ"},
        {"family": "continuous", "items": ["me", "mae", "rmse", "corr"]},
        {"family": "spatial",    "items": ["fss"],
         "levels": [40], "half_windows": [5, 10, 15, 20, 25, 30]},
        {"family": "moments",    "items": ["tmmsss"]},
    ],
}
```

### 必填字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `group_id` | str | |
| `name` | str | 展示名 |
| `version` | int | 影响检验结果的字段一变，此值必须变 |
| `status` | str | `active` / `retired` |
| `truth.name` | str | 传给 `read_truth` |
| `truth.variable` | str | |
| `truth.unit` | str | |
| `truth.cadence_minutes` | int | 实况时间步长 |
| `space.bbox` | [float]×4 | `[西, 南, 东, 北]` |
| `space.resolution` | [float]×2 | 度 |
| `space.projection` | str | 如 `EPSG:4326` |
| `time.timezone` | str | 如 `UTC` |
| `time.init_from` / `init_to` | str | 带时区 ISO 8601 |
| `forecasts[].name` | str | 传给 `read_forecast` |
| `forecasts[].unit` | str | |
| `forecasts[].leads_minutes` | [int] | 该模型实际产出的时效 |
| `metrics[].family` | str | 见下 |
| `metrics[].items` | [str] | |
| `metrics[].levels` | [float] | 见下 |
| `metrics[].half_windows` | [int] | 见下 |

### 指标族的参数

| family | 必需 | 可选 |
|---|---|---|
| `yes_or_no` | `levels` | `unit`（默认取实况单位） |
| `continuous` | — | `levels` |
| `spatial` | `levels`、`half_windows` | — |
| `moments` | — | — |

- `family` 取值封闭，其他值应拒绝。
- 同一族内 `levels` 的区间不得相交。

---

## 3. `read_truth(name, time)`

```python
read_truth(name: str, time: str) -> xarray.DataArray
```

| | |
|---|---|
| 输入 | 数据名称；有效时刻（带时区 ISO 8601） |
| 返回 | 二维场，`xarray.DataArray` |
| 错误 | 名称不存在 / 时刻不在范围内 / 该时刻无数据 → 抛异常 |

---

## 4. `read_forecast(name, init_time, lead_minutes)`

```python
read_forecast(name: str, init_time: str, lead_minutes: int) -> xarray.DataArray
```

| | |
|---|---|
| 输入 | 预报名称；起报时刻（带时区 ISO 8601）；时效（分钟） |
| 返回 | 二维场，`xarray.DataArray` |
| 错误 | 名称不存在 / 起报时刻或时效没有产出 → 抛异常 |

---

## 5. `read_truth` / `read_forecast` 返回的 xarray

### 维度与坐标

```python
da.dims                       # ("lat", "lon") 或 ("y", "x")，全部接口统一一种
da.coords["lon"].values       # 一维、严格递增、单位「度」
da.coords["lat"].values       # 一维、严格递增、单位「度」
```

- 坐标系：`EPSG:4326`
- 不使用二维 lon/lat 网格
- 时间不作为维度；有效时刻放 `attrs["valid_time"]`

### 属性

```python
da.attrs["units"]      # 单位，按 CF
da.attrs["variable"]   # 变量名，与 group 中的 truth.variable / forecasts[].name 对应
```

### 缺测

```
格点缺测          ->  NaN
整个时刻没有数据   ->  抛异常（不返回全 NaN 的场）
```

### 内存

`da.values` 为内存中的 numpy 数组，不使用 dask 等惰性数组。

---

## 6. 通用约定

**错误**

```
名称 / group_id 不存在         ->  抛异常
时刻不在范围内                 ->  抛异常
该时刻或该时效没有产出          ->  抛异常
```

使用统一的、带可读原因的异常；不返回 `None` 或空对象。

**格式**

```
bbox        四个数，顺序固定为 [西, 南, 东, 北]
绝对时刻     带时区的 ISO 8601
时效        分钟，整数
```

**名字与版本**

- 数据名称（`truth.name`、`forecasts[].name`）必须全局唯一。
- `group_id` + `version` 唯一确定一组检验。影响检验结果的字段（`bbox`、`resolution`、`cadence_minutes`、单位、变量名、指标参数）发生任何变化，`version` 必须递增。

**存储**

```
/run/products/diagnostics.sqlite
```
