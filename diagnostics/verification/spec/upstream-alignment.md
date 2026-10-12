# 对齐上游：从 `review/verify/` 到 `diagnostics/`

> 陈宬 2026-10-10。读的是上游 `origin/main`（`d252802`）和分支 `origin/feat/diagnostics`（`e9ebe9e`）。
> **本文只记录事实和计划，代码还没动。**

## 一、上游的目录分工

```text
adapters/catalog.json                 web 索引
adapters/<data-id>/source.py          一个数据源（6 个：cma_radar / fy4b / gk2a /
                                      himawari9 / mtg / sevir）
adapters/<data-id>/<model-id>/        一个模型，程序是 adapter.py
                                      （sevir/{wadepre,nowcastnet,exprecast}）
adapters/__main__.py                  可运行入口
artifacts/                            权重下载缓存，不进 git

contracts/data/<id>.json              数据源声明
contracts/models/<data-id>/<id>.json  模型声明
contracts/examples/                   ← 被 README 引用，但**文件还不存在**
interfaces/                           纯 Python 契约，只用标准库
utils/paths.py                        路径解析（跨平台守卫）
utils/weights.py                      Hugging Face 权重校验/下载

diagnostics/                          ← 分支 feat/diagnostics 上，**未合并**
├── README.md                         （中文）
├── Dockerfile / requirements.txt
└── visualization/                    Tiantian 的可视化 MVP（nib_visualization 包）
```

## 二、契约（`interfaces/data.py`，只用标准库）

| 类型 | 作用 |
|---|---|
| `DataCapabilities` | 静态声明：`id/version/title/instrument/channels/spatial/native_cadence_minutes/formats` |
| `ChannelSpec` | `mode = "open" \| "closed"`；closed 时 `variables` 是白名单，非空 |
| `SpatialSpec` | `crop: bool`，`bbox_order = (west, south, east, north)` |
| `DataRequest` | `source/data_dir/data_root/initial_time/history_steps/interval_minutes/channels/bbox` |
| `DataBundle(dict)` | 键：`source / request / data / source_files / metadata`。**不 import xarray** |
| `DataAdapter(ABC)` | `capabilities()`（不开文件）/ `validate()` / `load()`（不下载） |

配套函数：`request_from_mapping`、`validate_request`、`loader_config`、`bundle_from_parts`。

**一个真实实现**（`adapters/sevir/source.py`，156 行）：

```python
class Source(DeclaredDataAdapter):
    CAPABILITIES = DataCapabilities(...)        # 类常量

    def validate(self, request):
        super().validate(request)               # 公共字段
        if request.interval_minutes != 5:       # 只加自己那条
            raise ConstraintError(...)

    def load(self, request):
        self.validate(request)
        config = loader_config(request)         # 绝对路径，且必须存在
        import h5py, numpy as np                # 第三方包在函数里才 import
        ...
        return bundle_from_parts(caps.id, request, data, source_files, metadata)

ADAPTER = Source()                              # 模块级单例
```

## 三、三个必须记住的发现

### 1. 真值序列在 `metadata` 里

```python
metadata={
    "target": target,                                       # (20, 128, 128)
    "target_lead_minutes": [5 * (6 + i) for i in range(20)],  # [30..125]
    "context_times": times,
}
```

SEVIR 的 `targets` 是**未来的实况**。**`target` + `target_lead_minutes` 就是我们的「实时效排列的真值序列」** —— 拿到它就能算 TS/bias/FSS。这是接入点。

### 2. `units = "raw"` —— **已定：算一个单位，不做特殊处理**

```python
VARIABLE_UNITS = "raw"      # SEVIR VIL 还原成 [0, 255]
```

本模块的单位一律**按字符串精确比较**，`"raw"` 就是普通字符串。
**不加白名单、不加特例、不归一化** —— 为某个字符串开口子就等于废掉「单位不一致就拒绝」这条不变量。
后果是对的：SEVIR 的 VIL 只能和同样声明 `"raw"` 的真值相比。
**真有实际问题，是上游改声明，不是我们在下面接住。**

### 3. adapter 不做重采样，缺数据是异常

```python
if request.interval_minutes != NATIVE_CADENCE_MINUTES:   # 时间上必须原生步长
    raise ConstraintError(...)
raise FileNotFoundError(f"SEVIR sample not found for start time {wanted}")
```

`CROP = False`（卫星只记录 bbox 不裁剪；CMA 雷达可以裁剪）。
→ **重采样是我们的活**（和原设计一致）；**没有静默的空结果**（和"缺口就是缺口"一致）。

## 四、我们 ↔ 上游

| 上游 | 我们 | 处理 |
|---|---|---|
| `DataAdapter.load()` | `FieldAccess.get_observation/get_prediction` | **换签名** |
| `DataBundle["data"][time][var]` | `Field(data, coords, valid_time, unit, variable)` | 加一个转换层 |
| `metadata["target"]` | 真值序列 | 直接用 |
| `DataCapabilities` | `manifest.json` 解析结果 | 字段对得上 |
| `ConstraintError` | `ConfigError` / `MatchResult` | 换异常类 |
| `capabilities.channels.mode` | **我们没有对应物** | 吸收：正好表达"这个源能不能给某个变量" |
| `resolve_data_dir` | 我们不管路径 | 采用它的跨平台守卫（拒绝外系统的绝对路径、拒绝 `..` 逃逸） |

## 五、计划

**第 0 步（前置）**：切到 `feat/diagnostics` 分支，代码搬到 `diagnostics/verification/`，
和 `diagnostics/visualization/` 并列（各自 `pyproject.toml` + `src/` + `tests/`）。

**第 1 步**：`access/` 改成吃 `interfaces/data.py` 的契约
- `FieldAccess` → 实现 `DataAdapter`
- 加 `bundle_to_fields(bundle) -> {time: {var: Field}}`
- 异常映射：`ConstraintError` / `FileNotFoundError` → 我们现有的 `ConfigError` / `FieldUnavailable`

**第 2 步**：清单改成读 `adapters/catalog.json` + `contracts/data/*.json`，不再自己扫目录

**第 3 步**：实现 `diagnostics` 的入口（上游写的是"暂未实现"）
```
python -m diagnostics --config /run/request.yaml
  读 observation / forecasts / metrics
  写 /run/products/diagnostics.sqlite + metrics.json
```

**第 4 步**：填 `contracts/examples/diagnostics-request.example.yaml`（上游引用了但没写）

## 六、已定的 / 还没定的

### 已定（2026-10-10）

**1. 放在 `diagnostics/`（容器），不走 `review/`。**
上游两份文档互相矛盾：`AGENTS.md` + `docs/modules.yaml` 说 `review/`（主机）；
`diagnostics/README.md` 说可视化和指标计算都在容器里。**选定后者**，即
切到 `feat/diagnostics` 分支，代码放 `diagnostics/verification/`，和 `diagnostics/visualization/` 并列。

**2. `units = "raw"` 算一个单位，我们不做特殊处理。**
单位一律按字符串精确比较，`"raw"` 就是普通字符串。
**不加白名单、不加特例、不做归一化。**
理由：单位不一致就拒绝是本模块的核心不变量，为某一个字符串开口子就把它废了。
**如果 `"raw"` 造成实际问题，那是上游该改声明，不是我们在下面接住。**
相应地：SEVIR 的 VIL 只能和声明 `"raw"` 的真值相比，和声明 `dBZ` 的真值比不了 —— 这是对的。

### 还没定（不该我拍）

**3. 存储粒度**：一工作区一库（我们现在）还是一调用一库（上游写的 `/run/products/diagnostics.sqlite`）？
三个选项：① 只有 per-run ② per-run + 工作区汇总（建议）③ 只有工作区库。

**4. `contracts/README.md` 明说 diagnostics request「不是 evaluation draft」** ——
两者的关系要问清。

**5. `docs/modules.yaml` 里 D1 还是 `status: planned`、`owner: null`**；
而 `nib-architecture/modules.yaml` 里是 `exists`。两处不一致，认领正好补上。

## 七、可以直接跑的

```sh
python -m adapters --config tests/adapters/requests/cma_radar/example_success.yaml \
                   --data-root "$NIB_DATA_ROOT"
```

请求 YAML 在 `tests/adapters/requests/<source>/example_*.yaml`（success / missing_time /
missing_channel 三种）。**没有数据文件时会在"文件不存在"处失败，但校验那一段会跑完** ——
可以用来观察错误信息的形态。

## 八、上游自己留的缺口

- `adapters/README.md` 末尾 `TODO(ci)`：外部扫描比对 `contracts/*.json` 与 `adapters/catalog.json` **未实现**
- `contracts/examples/diagnostics-request.example.yaml` **不存在**（README 两处引用它）
- `diagnostics` 入口 **未实现**
