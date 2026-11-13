# 检验接口说明（本模块提供）

本文定义检验模块对外提供的接口。**实现在我们这边**（`nib_verify`）。
调度方按此调用即可，不需要读库、不需要自己算指标。

一轮 run 的调用顺序：

```
RunAccess(run_dir) → VerificationConfig(...) → todo(...) → [SingleInstanceLock] → run_tasks(...) → query_scores(...)
```

---

## 1. `todo()` — 这批起报时刻要算什么

```python
todo(access, cfg, *, init_times: Sequence[datetime]) -> list[RunRef]
```

| | |
|---|---|
| 输入 | `access` 数据访问对象；`cfg` 配置；`init_times` 起报时刻列表 |
| 返回 | `RunRef(member: str, init_time: datetime)` 列表 |
| 过滤 | 成员的 `active_from/active_to` 之外的跳过；`access` 里没有的成员跳过 |
| 不查库 | 它只做规划，不知道哪些已完成；完成状态由 `run_tasks` 判定 |

批量回算时用它算清单，再交给 `run_tasks`。

---

## 2. `run_tasks()` — 执行（幂等）

```python
run_tasks(access, store, cfg, runs: Iterable[RunRef], *,
          force: bool = False,
          on_progress: Callable[[str], None] | None = None) -> RunStats
```

**幂等**：`task` 表里已记为 `done` 的任务直接跳过，不调数据源。
一个子集重跑是免费的（`force=True` 才重算）。

`on_progress` 每完成一个 `(member, init, lead)` 回调一次：
`"nowcastnet 2026-07-01T00:00:00Z +60 (12 done / 60 total)"`

返回 `RunStats`：

| 字段 | 含义 |
|---|---|
| `total` | 本轮任务总数 |
| `ran` | 实际计算数 |
| `skipped` | 已完成跳过数 |
| `unavailable` | 数据源声明该帧不可用 |
| `failed` | 计算失败数 |
| `obs_missing` | 实况缺测（记入 `obs_gap`，不算失败） |
| `seconds` | 耗时（秒） |
| `messages` | 前 50 条说明 |

`RunStats.as_dict()` 可直接落 JSON；`summary.json` 的 `status` 规则：
`failed>0 → "failed"`，`unavailable>0 → "partial"`，否则 `"succeeded"`。

---

## 3. `SingleInstanceLock` — 同一库只允许一个写者

```python
with SingleInstanceLock(store_path.parent, name="verify.lock"):
    run_tasks(...)
```

第二个持有者抛 `Locked`。**陈旧锁自动接管**（进程已死则覆盖），不会永久阻塞。

---

## 4. `query_scores()` — 聚合出数

```python
query_scores(store, cfg, *, method: str,
             s: Mapping[str, Any] | None = None,
             g: Sequence[str] | None = None,
             limit: int = 20_000) -> dict
```

| | |
|---|---|
| `method` | 指标名，如 `mae` `rmse` `ts` `ets` `fss` |
| `s` | 选择条件，如 `{"level": [30], "half_window": [10]}` |
| `g` | 分组键，如 `["member","lead_minutes","level"]` |
| `limit` | 行数上限，默认 20000；超限抛错（提示收窄时段或换更短的轴） |

返回：

```python
{
  "meta":   {...},            # 方法、参数、分组键、尺子指纹
  "coverage": {...},          # 该组数据的覆盖情况
  "sample": {"n_records": n, "n_valid": m},
  "dims":   [...],            # 实际分组键
  "records": [                # 每行：分组键 + value + n_valid
      {"member": "nowcastnet", "lead_minutes": 60, "level": 30.0,
       "value": 0.482, "n_valid": 2160.0},
      ...
  ],
}
```

`value` 为 NaN 时输出 `null`（缺测、或该组合分母为零），不是 0。

参数只对所属族有效：`level` 用于 `yes_or_no`/`spatial`，`half_window` 用于 `spatial`。
用错会明确报错，不会静默返回空。

---

## 5. 出图（服务端渲染 SVG）

两步：`chart_for` 取数+枢轴，`line_chart` 渲染。

```python
chart_for(store, cfg, *, method: str,
          s=None, x="lead_minutes", series=("member",),
          order=(), limit=20_000) -> Chart

line_chart(chart, *, width=760, height=440,
           y_min=None, y_max=None, kind="line") -> str     # SVG 文本
```

`x` 与 `series` 是展开维度，两者都必须可分组（与表格同一套检查、同一套报错）。
`kind` 只有 `line` / `bar`；`bar` 的 y 轴从 0 起（长度才有意义）。

多子图 / 网格图另有 `small_multiples`、`mesh_for`、`mesh_svg`、`chart_panels`、`columns_for`（都在 `nib_verify.report` 的 `__all__` 里）。

图例输出在页面上（可点击隐藏），画布内只留图。

---

## 6. 配置对象（构造一次，传给上面三个函数）

```python
Ruler(ground_truth, unit, time_step_minutes, extent, resolution, crs="EPSG:4326")
Member(name, variable, unit, leads, active_from=None, active_to=None)
MetricGroup(family, items, levels=(), half_windows=())
VerificationConfig(group_id, name, ruler, members, metrics)
```

| 字段组 | 可变性 |
|---|---|
| `ruler`（`ground_truth`/`extent`/`resolution`/`crs`/`time_step_minutes`） | **不可变** |
| `members`（预报成员名单） | 可增删 |
| `metrics`（指标清单） | **只增不改** |
| `name` | 纯展示字段 |

序列化（上游把配置落盘为 `config.json`，调度方读回同一份）：

```python
cfg.to_json() -> str                                  # 落盘
VerificationConfig.from_json(text) -> VerificationConfig   # 读回
```

族名封闭：`yes_or_no` · `continuous` · `spatial` · `moments`。
`ruler_fingerprint` 由 `ruler` 算出：**指纹不同不可混比**（跨域、跨网格的数据不能放进同一组对比）。

---

## 7. `RunAccess` — 数据读取（上游实现，我们消费）

`nib_verify/access/stored.py` 只是薄适配层，底层是**上游**的 `utils.stored_fields.StoredFields`。
因此在集成容器里（`/app/utils` 在路径上）可直接用；在本包目录下单独跑，需要把上游仓库根的 `diagnostics/` 加进 `sys.path`。

```python
RunAccess(directory)

.capabilities()                                  # {"model_ids": [...], ...}
.list_observations(name, t0, t1)                 # 实况时刻
.get_observation(name, valid_time)               # xarray
.list_predictions(model_id, init_time)           # 该次起报的时效
.get_prediction(model_id, init_time, lead)       # xarray
```

时间分辨率固定，**不做运行时插值**；尺子必须是实况的网格。

---

## 8. 最小接入示例

```python
from datetime import datetime, timezone
from nib_verify.access.stored import RunAccess
from nib_verify.config.schema import Member, MetricGroup, Ruler, VerificationConfig
from nib_verify.query import query_scores
from nib_verify.runner import RunRef, SingleInstanceLock, run_tasks, todo
from nib_verify.store import Store

access = RunAccess(run_dir)
cfg = VerificationConfig(
    group_id=run_id,
    name="20260701@00Z",
    ruler=Ruler("cma_radar", "dBZ", 6, (-190500, -190500, 190500, 190500), 3000.0),
    members=(Member("nowcastnet", "cref", "dBZ", tuple(range(6, 121, 6))),),
    metrics=(MetricGroup("yes_or_no", ("ts", "ets", "bias"), levels=(10, 30, 50)),),
)

init = datetime(2026, 7, 1, tzinfo=timezone.utc)
refs = todo(access, cfg, init_times=[init])          # 规划
with Store(store_path) as store:
    with SingleInstanceLock(store_path.parent):      # 防并发
        stats = run_tasks(access, store, cfg, refs, on_progress=print)
    result = query_scores(store, cfg, method="ts",
                          g=["member", "lead_minutes", "level"])
print(stats.as_dict(), len(result["rows"]))
```

---

## 9. 分工边界

| 我们负责 | 调度方负责 |
|---|---|
| 指标公式、regrid、可加量、缺测判定 | 何时触发（cron / 订单 / 手工） |
| 聚合、分组、出数、出图 | 批量编排、重试、进度记录 |
| 幂等（`done` 跳过）、单实例锁 | 并发度、库的生命周期与保留策略 |
| 库表结构（`Store` 自动建表） | 库放哪、挂载给谁 |

---

## 10. 兼容性

- 多个 run 共用一库：按 `group_id` 隔离，互不影响。
- 库表由 `Store` 打开时自动创建（`nib_verify/store/schema.sql`）。
- 包版本见 `nib_verify.__version__`。
- 已核验：真实数据一次 run 的产物与上游既有产物 **3653 个数值逐值零差异**。

---

## 11. 数据到达监控（`nib_verify.monitor`）

检验结果能回答"这一帧算过没有"，回答不了"这一帧到底到没到"。两件事必须分开，
否则页面上的空白分不清是**数据缺失**还是**程序没跑**。数据到达监控 = 一条命令
（探针）定时去问数据源，把答案写进 `arrival` 表；页面（同一个服务的 Coverage 标签）
只读这张表。

### 11.1 `probe_arrivals()` — 问数据源，写答案

```python
probe_arrivals(access, store, cfg, *,
               since: datetime | None = None,
               until: datetime | None = None,
               window_hours: float = 6.0,
               dataset: str | None = None) -> ProbeStats
```

| | |
|---|---|
| 输入 | `access`（能问数据源的 field-access，通常 `http`）；`store`；`cfg`（定 cadence）|
| 行为 | 按 `ruler.time_step_minutes` 逐帧走窗口 → `access.list_observations(name, t0, t1)` 问数据源实际有哪些帧 → 每帧写 `arrived` / `missing` |
| 窗口 | 默认"现在往前 6 小时"；`since`/`until` 可显式指定（回填历史）|
| 返回 | `expected / arrived / missing / written` + `messages`（`ProbeStats.as_dict()` 可直接落 JSON）|
| 幂等 | 同一窗口跑多少次，表里结果一样（`ON CONFLICT DO UPDATE`）|
| 错误 | 数据源不可用 → `messages` 里记原因，**不抛异常**：源挂了是一次发现，不是一次崩溃 |

### 11.2 `nib_verify probe` — 同一件事的命令行

```bash
python -m nib_verify probe --config <config.json> --root <store 目录>     --access http --data-url http://<数据服务> --window-hours 6
```

| 退出码 | 含义 |
|---|---|
| `0` | 全到（或源不可用——此时 `messages` 里写明原因）|
| `1` | 有缺帧（可据此触发告警，也可忽略）|

### 11.3 `arrival` 表（探针写、页面读）

```sql
CREATE TABLE arrival (
    valid_time TEXT NOT NULL,      -- 那一帧的时刻
    dataset    TEXT NOT NULL,      -- 观测名（如 cma_radar）
    kind       TEXT NOT NULL,      -- obs | forecast
    state      TEXT NOT NULL,      -- arrived | missing
    checked_at TEXT NOT NULL,      -- 探针何时确认的
    PRIMARY KEY (valid_time, dataset)
);
```

**没有 `group_id`**：某帧到没到是数据源的属性，不是哪个配置的属性。
**老库不用迁移**：`Store` 打开时按需建这张表。

### 11.4 最小接入示例（调度方照抄）

```python
# 定时任务（cron 每 15 分钟）调一次；窗口 ≥ 2 × 触发间隔，否则帧会掉缝里
from nib_verify.monitor import probe_arrivals
from nib_verify.store import Store

with Store("/data/verification/verify.sqlite") as store:
    stats = probe_arrivals(access, store, cfg)   # 默认回看 6 小时
if stats.missing:
    log.warning("%s missing on %s", stats.dataset, stats.checked_at)
```

```bash
# 或直接用命令行（宿主机 cron 一行）
*/15 * * * * cd /opt/nib && python -m nib_verify probe     --config /data/configs/<gid>.json --root /data/verification     --access http --data-url http://field-service:8080 --window-hours 3
```

### 11.5 分工边界

| 我们负责 | 调用方（上游）负责 |
|---|---|
| 采样逻辑、幂等、`arrival` 表结构 | **什么时候调、多久调一次**（cron / handler 定时作业）|
| 页面与 3 个查询接口（随服务自带，不用接入）| **数据服务地址**（`GET /observations`，见 `field-access-http.md`）|
| 老库自动建表 | **库放哪、是否跨 run 持久**（不持久则"全部时间/近一周"两列无意义）|

### 11.6 兼容性

| 场景 | 行为 |
|---|---|
| 无 `arrival` 行 | 页面回退为"由检验结果反推"，`obs_source = "derived"`，照常可用 |
| 有 `arrival` 行 | 页面用探针答案，`obs_source = "probe"`，能区分"未到 / 未跑" |
| 同一窗口重复跑 | 结果不变（幂等）|
| 数据源短暂不可用 | 不写新行、不崩溃，`messages` 记录原因 |
