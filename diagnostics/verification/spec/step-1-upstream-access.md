# 第 1 步的接口草稿：`access/upstream.py`

> 写给下一轮直接填。**这是草稿，不是实现。**
> 前置：`spec/upstream-alignment.md`（契约与映射）· `spec/step-0-move.md`（第 0 步已完成）

## 0. 一个必须先纠正的假设

我原先写"把 `FieldAccess` 换成 `DataAdapter`" —— **这个说法不完整**。看清楚之后，两边管的是**两件不同的事**：

```
真值（observation）  ←  DataAdapter          上游有接口，我们能用
预报（forecast）     ←  ????                  上游**没有**给我们接口
```

`adapters/sevir/source.py` 返回的 `metadata["target"]` 是**观测的未来帧**（SEVIR 的 targets），**不是模型预报**。
模型预报要跑推理 —— 而 `docs/modules.yaml` 写着 D1 `does_not_own: [Inference]`。

**所以 `upstream.py` 只管真值那一半。** 预报那一半从哪来，见下面 §4 —— **这是要问上游的**。

而上游的 `diagnostics` 请求文件其实已经给了答案的一半：

```yaml
observation: /run/inputs/observation         ← 真值：一个目录
forecasts:
  - id: wadepre
    path: /run/inputs/forecasts/wadepre      ← 预报：**平台写好的文件，给路径**
```

**真值走 adapter，预报走"平台写好的文件"。** 我们的 `ReplayAccess` 已经是后者的形状。

## 1. 要做的东西

```python
# diagnostics/verification/nib_verify/access/upstream.py
"""Read the truth through the platform's own data adapter contract."""

from interfaces.data import (
    DataAdapter, DataCapabilities, DataRequest,         # 上游契约
    request_from_mapping, loader_config,
)
from interfaces.errors import ConstraintError

from .base import Capabilities, Coords, Field, FieldAccess, FieldUnavailable


class UpstreamAccess(FieldAccess):
    """A FieldAccess over one upstream DataAdapter.

    上游一次读一批（load 返回整个 bundle），我们一次取一个时刻 ——
    所以这里要缓存：同一个 (source, initial_time) 只 load 一次。
    """

    def __init__(self, adapter: DataAdapter, request: DataRequest,
                 *, units: dict[str, str] | None = None) -> None:
        self._adapter = adapter
        self._request = request
        self._units = dict(units or {})
        self._bundles: dict[str, DataBundle] = {}       # initial_time -> bundle

    # ---- 缓存 ----
    def _bundle(self) -> DataBundle: ...                 # 未命中就 adapter.load(...)
```

## 2. 五个方法各自从哪来

| `FieldAccess` 方法 | 从 bundle 的哪里取 |
|---|---|
| `capabilities()` | `DataCapabilities` → `Capabilities(cadence_minutes=native_cadence_minutes, observation_names=(id,), model_ids=(…), units=self._units)` |
| `list_observations(name, t0, t1)` | `metadata["context_times"]` 里落在 `[t0, t1]` 的 |
| `get_observation(name, valid_time)` | `bundle["data"][iso][variable]` → `Field` |
| `list_predictions(model, init)` | **不在这个 adapter 里** —— 见 §4 |
| `get_prediction(model, init, lead)` | **不在这个 adapter 里** —— 见 §4 |

**`Field` 的构造**（`bundle["data"][time][var]` 是一个裸 ndarray）：

```python
Field(
    data=array,
    coords=self._coords(),          # 见 §3
    valid_time=_parse(iso),
    unit=self._units.get(var, ""),  # ← 见 §3 的注意
    variable=var,
    name=name,
    mask=None,                      # 上游不传 mask；缺数据是抛异常，不是 mask
)
```

## 3. 三处要注意

**① 坐标从哪来 —— 这是缺口**

`DataBundle` 里**没有坐标**（bundle 是 `data / source_files / metadata`，只有数组）。
我们 `Field` 必须带 `Coords`。

三个来源，按可信度：
1. `DataCapabilities.spatial`（只有 `crop` 和 `bbox_order`，**没有分辨率和范围**）
2. `DataRequest.bbox`（**这是我们请求时给的**，不是数据源的声明）
3. **实际数组的 shape** 配上 `bbox` → `coords_from_extent(bbox, shape)`（我们已经有的函数）

**取 3：用请求的 bbox + 数组 shape 造网格**，并在 spec 里注明"坐标是我们推的，不是上游给的"。
**这也是要问上游的**：`contracts/data/<id>.json` 里有没有分辨率/范围字段？

**② `unit` 从哪来**

`DataBundle` 里**也没有单位**。`DataCapabilities.channels.variables` 里**有** `VariableSpec.units`：

```python
units = {v.id: v.units for v in caps.channels.variables}
```

**但这个只有 `closed` 模式才有意义**（`open` 模式的 `variables` 是空的）→ `open` 时单位只能靠调用方给，**要记成已知限制**。

**③ 单位比较照旧按字符串**

`"raw"` 就是普通字符串（已定，见 `upstream-alignment.md` 六.2）。**不加特例。**

## 4. 预报那一半 —— **要问上游**

`get_prediction` 需要的"模型预报"在 `DataAdapter` 里没有。候选：

| 候选 | 说明 |
|---|---|
| **A. 平台写好的文件** | `diagnostics` 请求里 `forecasts: [{id, path}]` —— 说明平台会把预报落盘。**格式未知** |
| B. 跑模型适配器 | `adapters/sevir/wadepre/adapter.py` 能把 bundle 变成 tensor 跑推理 —— 但那是**推理**，`does_not_own` 明确不算我们的 |
| C. 我们的 `ReplayAccess` | 读文件；**只要知道格式就能接** |

**要问的**：模型跑完之后，handler 把预报写到哪里、什么格式？（manifest 里有 `output[].dtype/shape/cadence_minutes/frame_count`，可能够描述 NetCDF/HDF5 的布局。）

**在得到答案之前**：`get_prediction`/`list_predictions` **先抛 `FieldUnavailable` 并带上原因**（"forecast files are not wired yet"），**不要瞎猜格式**。

## 5. 异常映射

| 上游 | 我们 | 语义 |
|---|---|---|
| `ConstraintError` | `ConfigError` | 配置/请求错了 —— **拒绝**，不要往下走 |
| `FileNotFoundError` | `FieldUnavailable` | 这个时刻没有 —— **记成 unavailable 任务**，这正是"缺口就是缺口" |
| 其它异常 | 原样透出 | 不要吞 |

## 6. 谁改动了 —— 影响面

**新增一个文件。核心链路 0 改动。**

调用点只有四个（第 0 步前统计过，搬家不影响）：

```
core/planner.py:286     list_observations
runner/executor.py:178  get_observation
runner/executor.py:187  get_prediction
cli.py:331              list_predictions
```

`UpstreamAccess` 实现同一个 Protocol，**所以这四个地方一行都不用改** —— 换个 provider 传进去即可（`access/factory.py` 加一个分支）。

## 7. 测试清单

**不依赖真数据文件**（数据文件在 git 外，CI 上不会有）：

1. `test_capabilities_come_from_the_adapter` —— 字段逐个对
2. `test_a_second_request_for_the_same_init_time_does_not_load_again` —— **缓存**（用假的 adapter 数调用次数）
3. `test_the_context_times_are_the_observation_times` —— `data` 的键 = `metadata["context_times"]`
4. `test_a_time_that_is_not_in_the_bundle_is_unavailable` —— **不是空 Field，是 `FieldUnavailable`**
5. `test_a_constraint_error_stays_a_configuration_error` —— 异常映射表照 §5
6. `test_the_field_carries_the_sources_unit` —— 从 `VariableSpec.units` 来
7. `test_predictions_say_they_are_not_wired_yet` —— §4 那条，**明确地抛，不装懂**
8. `test_an_open_channel_source_has_no_units_to_offer` —— §3②的限制记成测试

**用一个假的 `DataAdapter`（返回构造好的 `DataBundle`）**，不需要 h5py、不需要 SEVIR 文件、不需要网络。

## 8. 文件放哪

```
diagnostics/verification/
├── nib_verify/access/upstream.py       ← 新增
└── tests/test_upstream_access.py       ← 新增
```

**不改** `base.py`（契约不动）· **不改** `planner.py` / `executor.py`（调用点不动）。
