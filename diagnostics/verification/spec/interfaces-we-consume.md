# 检验模块请求的接口标准

> 给上游。我们不再产生配置，只**消费**配置和数据 —— 所以本文定的是**接口的形状**，实现在上游。
> 分成两组：**编目接口**（返回字典）和**数据接口**（返回 xarray）。

## 0. 我们的位置

```
平台（配置 + 管理 + 数据）  ──►  接口  ──►  我们（算指标、存结果、出图）
                                  ↑ 本文只定这一层
```

**我们不定义 group、不改配置、不管数据在哪。** 我们只问两个问题：

1. **有哪些检验、每个检验是什么？** → 字典
2. **某个检验要的实况/预报数据在哪？** → xarray（按名字 + 时间 + 时效取）

---

## 0b. 我们的范围（2026-10-12 定）

**我们只做"消费 → 计算 → 存 → 看"，不做配置的产生与管理。**

```
平台（配置 + 管理 + 数据）──► 接口 ──► 我们：算指标 → 存 SQLite → 查询汇总 → 出图
```

### 因此作废的（原先以为是我们的）

| 作废 | 原先干什么 |
|---|---|
| `catalog/packages.py` | 解析 `manifest.json`、编目 |
| `catalog/matching.py` | 把尺子和模型名单配到一起 |
| `catalog/hostconfig.py` | 读平台配置、路径守卫 |
| `catalog/library.py` | 配置库的增删改查 |
| `access/factory.py` | 多 provider 切换（只剩一个来源） |
| 页面的 `New config` 向导 | 建配置 |
| 页面的配置管理部分 | 列表、排序、过滤、分页、Retire |
| 「5 并排比较两条配置」 | 管理界面挂着的待办 |

### 保留的（核心资产）

| 保留 | 为什么 |
|---|---|
| `core/` 算法 | 指标、中间量（H/F/M/C、T/E/A/S）、重采样、网格 —— 最值钱的部分 |
| `store/` | SQLite 写入 + **在 SQL 里聚合** |
| `report/` | **服务端渲染 SVG** |
| `query/` | 查询服务 |
| `runner/` | **仍然要** —— 一次运行里仍有很多任务（起报时刻 × 时效 × 等级），计划、幂等、缺口都还在 |
| `config/schema.py` 的**校验**部分 | **仍然要**：从 `get_group()` 拿到配置后照样要检查 —— 单位不一致就拒绝、等级区间不相交、步长是硬不变量。**"配置不是我写的"不等于"配置不用检查"** |

### 页面变成「一次运行一页」

没有跨 run，于是没有：运行选择器、配置库、向导。
剩下的正是打磨最久的部分：查询面板 + 图表 + 表格 + CSV。

## 1. 四个接口

| # | 接口 | 输入 | 输出 |
|---|---|---|---|
| ① | `list_groups()` | — | `list[str]`（group_id 列表） |
| ② | `get_group(group_id)` | group_id | **字典**（见 §3） |
| ③ | `read_truth(name, time)` | 数据名称 + 时间 | **xarray**（见 §4） |
| ④ | `read_forecast(name, init_time, lead_minutes)` | 数据名称 + 起报时间 + 时效 | **xarray**（见 §4） |

**① 和 ② 是编目，③ 和 ④ 是数据。** 两者都**不返回路径、不返回文件名、不暴露存储格式**。

---

## 2. 接口 ①：`list_groups()`

```python
["radar-csi-2025q3", "rain-fss-2025q3", "bt-me-2025q3"]
```

**只返回 id，不返回名称。** 名称在 ② 里 —— 因为列清单时不需要名称，而 ② 一定要被调用一次才知道内容。

---

## 3. 接口 ②：`get_group(group_id) -> dict`

### 3.1 你要的那五项，逐条落成字段

你点的是：**名称、实况名称、预报列表、区域范围、各种检验指标和检验参数**。

```python
{
    # ---- 身份 ----
    "group_id":  "radar-csi-2025q3",
    "name":      "Radar reflectivity CSI, 2025 Q3",   # 展示用
    "version":   1,                                    # 见 §6.3
    "status":    "active",                             # active | retired

    # ---- 实况（尺子的来源）----
    "truth": {
        "name":     "radar-composite",     # ★ 传给 read_truth 的名字
        "variable": "reflectivity",
        "unit":     "dBZ",
        "cadence_minutes": 6,              # ★ 时间步长，我们的硬不变量
    },

    # ---- 区域范围（尺子）----
    "space": {
        "bbox":       [112.0, 34.0, 120.0, 42.0],   # [西, 南, 东, 北]，显式写序
        "resolution": [0.1, 0.1],                    # 度。★ 没有它建不出尺子
        "projection": "EPSG:4326",
    },

    # ---- 时间范围 ----
    "time": {
        "timezone":  "UTC",                          # ★ 必须说清
        "init_from": "2025-07-01T00:00:00Z",
        "init_to":   "2025-09-30T23:00:00Z",
    },

    # ---- 预报列表 ----
    "forecasts": [
        {"name": "mlcast-det", "unit": "dBZ", "leads_minutes": [10, 20, 30, ..., 180]},
        {"name": "mlcast-alt", "unit": "dBZ", "leads_minutes": [10, 20, 30, ..., 180]},
    ],

    # ---- 指标与参数 ----
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

### 3.2 你的五项之外，还有五个字段是必需的

**写进去，否则我们算不对或算不了：**

| 字段 | 为什么必需 |
|---|---|
| **`truth.cadence_minutes`** | **时间步长是硬不变量。** 时间分辨率固定，我们**不做插值**。没有它，我们不知道该取哪些时刻、也不知道两个时刻之间的间隔对不对 |
| **`space.resolution`** | 只给 bbox 不够 —— 尺子是**实况的网格**。有范围没分辨率，我们只能拿预报自己的网格当尺子，**那是错的** |
| **`truth.unit` 和每个 `forecast.unit`** | 单位不一致的比较没有意义。我们要在**开始算之前**就拒绝，而不是算出一堆数再发现 |
| **`leads_minutes`** | 每个模型**实际产出**的时效。拿不到就让上游报错，不要我们猜一个等差数列 |
| **`time.timezone`** | 差 8 小时会让"这一天"从不同的地方开始，**而且看不出来** |

### 3.3 两条格式约定

```
bbox 的四个数一定是 [西, 南, 东, 北]     ← 写进文档，别靠默契
所有绝对时刻都是带时区的 ISO 8601        ← "2025-07-01T00:00:00Z"
时效一律用【分钟】的整数，不用小时、不用步数  ← lead=120 就是 2 小时
```

### 3.4 `metrics` 里 `family` 的取值是封闭的

```
yes_or_no | continuous | spatial | moments
```

**不认识的值请直接拒绝**，不要返回 —— 我们按族分派算法，一个没见过的族等于算不出来。
每个族的参数：

| family | 必需参数 | 可选 |
|---|---|---|
| `yes_or_no` | `levels`（阈值数组） | `unit`（阈值单位，默认取实况单位） |
| `continuous` | — | `levels`（只在限定阈值以上/以下算时用） |
| `spatial` | `levels`、`half_windows`（邻域半窗口，单位格点） | — |
| `moments` | — | — |

**同一族里 `levels` 的区间不许相交**（否则一个格点进两个桶，计数就错了）。

---

## 4. 接口 ③④：返回的 xarray 要达到什么

**"返回 xarray"不是契约** —— 不说清下面这些，会出现三种不同实现，而我们只在某一种上是对的。

### 4.1 形状

```python
# 一个二维场 = 一个 DataArray（不是 Dataset）
#   除非一次调用要返回多个变量，那才用 Dataset，且变量名必须是 data_vars 的键
da = read_truth("radar-composite", "2025-07-01T06:00:00Z")
da.dims        # ("lat", "lon")  或  ("y", "x")  —— ★ 全体接口统一一种，别混
```

### 4.2 坐标

```
经度、纬度：一维、严格递增、单位「度」
坐标系：    EPSG:4326（地理经纬度）—— 别的投影请显式说明
时间：      ★ 不要做成维度。我们按时刻逐次取；
            有效时刻放在 attrs["valid_time"] 或一个标量坐标 time
```

**不要用二维 lon/lat 网格** —— 能表达，但我们要多写一套分支，而且容易搞错行列顺序。

### 4.3 单位

```
attrs["units"]     ← 按 CF 约定
attrs["variable"]  ← 变量名（与 group 字典里的 truth.variable 一致）
attrs["name"]      ← 数据名称（与传入的 name 一致）
```

### 4.4 缺测 —— **这条最重要**

```
格点上缺测          →  NaN
整个时刻根本没有数据 →  ★ 抛异常，不要返回一个全 NaN 的场
```

**区别在于**：

| | 我们会理解成 |
|---|---|
| **抛异常** | 「这次运行没有产出」→ 记成一个缺口，**这是正常结果** |
| **返回全 NaN** | 「有数据，只是每个格点都缺」→ 我们会去算，算出 0 或 NaN，**而这个区别在图上和表上都看不出来** |

**我们区分「缺口」和「0」。** 把缺口当成 0，颜色深浅一样，结论已经错了。

### 4.5 必须**读进内存**，不要返回惰性数组

```
返回的 da.values 应当是 numpy 数组，不需要 .compute()
```

**如果返回 dask，每次 `.values` 都可能重新读盘** —— 取 20 个时效就开 20 次文件。
要么直接返回内存数组，要么**自己保证同一个 (name, init_time) 只读一次盘**。

（我们这边有 `probe.py` 数读取次数，这条可以验证，不用口头承诺。）

### 4.6 建议：直接声明"按 CF 约定"

上面的 4.2–4.4 基本就是 **CF Conventions**。**如果上游声明"返回 CF 兼容的 xarray"**，
坐标、单位、缺测三件事一次说清，比逐条列字段更省事，也更不容易漏。

---

## 5. 通用规则（两组接口都适用）

**① 名字的作用域要说清**

`read_truth("radar-composite", t)` 里的名字，**在什么范围内唯一？**

- 全局唯一？（推荐，最省事）
- 还是 `源 + 变量`（`cma_radar/reflectivity`）？

**现在 `contracts/data/<id>.json` 是"一个源一个文件"** —— 如果名字是源级的，
那一个源只能有一个变量，**这不够**。请在文档里明确。

**② 拿不到就报错，不要返回空**

```
名字不存在        → 抛异常（KeyError / 自定义 ConstraintError）
group_id 不存在   → 抛异常
时间不在范围内    → 抛异常
该时刻没有数据    → 抛异常（见 §4.4）
```

**用一个统一的、带可读原因的异常**，不要把三种情况揉成 `None`。

**③ 所有绝对时刻都带时区**

不带时区的 `datetime` 我们一律拒绝 —— 差 8 小时是个**没有症状**的错误。

**④ 不可变性**

`get_group` 返回的字典**我们不会修改**。反过来也请保证：一次调用返回的内容在本次运行内不变。
（`version` 变了就是另一个检验，见下。）

**⑤ 版本**

```
同一个 group_id + 同一个 version  ⇒  同一组检验，结果可比
改了 bbox / 步长 / 单位 / 变量名 / 指标参数  ⇒  version 必须变
```

**我们给结果盖一个指纹。指纹规则不同却是同一个 group_id，会把两组不可比的分数画在一张图上。**

---

## 6. 存储：一个 run 一个库（已定）

```
/run/products/diagnostics.sqlite        ← 上游 `diagnostics/README.md` 已经指定
--root /run                              ← 我们的 CLI 直接这么传
```

`store/db.py` 本来就是"打开一个路径"，**零改动**。**不需要"根目录"。**

### 只剩一条必须问清

> **`/run/products/` 里的文件，容器退出后会被平台收集吗？**
>
> 如果 `/run` 是容器内的临时层，进程一退文件就没了 —— **全都算对，结果却不存在**。
> 这是现在唯一真正卡住我们的事。

### 建议问的第二条

> **一次诊断运行是否就是「一个 group × 一个时间范围」？**
> 我们实测过：一条配置跑一年 = 1100 万行 / 2.1 GB。所以每次运行的规模要说清。

### 暂时不考虑跨 run

跨 run 查询会让页面要枚举并 `ATTACH` 多个库 —— **暂不做**。
（上游 suggested 的 per-run 形状正好不需要。）

## 7. 还需要上游回答的（除存储外）

| # | 问题 | 影响 |
|---|---|---|
| 1 | **名字的作用域**（§5①） | 决定 `read_truth` 的参数是几个 |
| 2 | **`dims` 用 `(lat, lon)` 还是 `(y, x)`** | 我们按这个写分支，**只写一种** |
| 3 | **预报的时效是每个模型不同，还是全局一套** | 决定 `leads_minutes` 放 group 还是放每个 forecast |
| 4 | **group 是"人建的"还是"系统按规则生成的"** | 系统生成的话，我们需要知道规则，好在出问题时能解释 |

## 8. 验收

**编目**

```python
ids = list_groups()
g = get_group(ids[0])
assert g["truth"]["cadence_minutes"] > 0          # 时间步长必须有
assert len(g["space"]["bbox"]) == 4               # [西,南,东,北]
assert g["space"]["resolution"] is not None       # 分辨率必须有
assert all(f["unit"] == g["truth"]["unit"] for f in g["forecasts"])   # 否则我们本来就会拒绝
assert all(m["family"] in {"yes_or_no","continuous","spatial","moments"} for m in g["metrics"])
```

**数据**

```python
da = read_truth(g["truth"]["name"], "2025-07-01T06:00:00Z")
assert da.dims in (("lat","lon"), ("y","x"))
assert da.coords["lon"].values[1] > da.coords["lon"].values[0]     # 严格递增
assert da.attrs["units"] == g["truth"]["unit"]
assert isinstance(da.values, np.ndarray)          # 读进内存了，不是 dask（§4.5）

try:
    read_truth(g["truth"]["name"], "1999-01-01T00:00:00Z")
except Exception as e:
    print("应当抛异常而不是返回空场:", e)          # §5②
```

**性能**

```
连取同一个 (name, init_time) 的 20 个时效 —— 文件只应被打开一次。
```
