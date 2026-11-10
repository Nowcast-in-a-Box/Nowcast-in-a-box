# NiB × MLCast Data Adapter

**Scope / 范围：** Seven European historical radar datasets are integrated with the **existing NiB Data Adapter**. Local historical samples are read through the original YAML → dispatcher → `DataBundle` workflow. Model inference and NiB Hub integration are **not** part of this acceptance.

**中文：** 已在原 NiB Data Adapter 中接入 7 组欧洲历史雷达数据。先下载小规模历史样本，再使用现有的 **YAML → 总调度器 → DataBundle** 流程读取。本次验收**不包括**模型推理与 Hub 中的模型配对。

## 1. Code changes / 代码修改范围

| Location / 目录 | Changes / 修改内容 |
|---|---|
| `adapters/catalog.json` | Add 7 MLCast source IDs / 注册七个数据源 |
| `adapters/mlcast_common.py` | Shared local Zarr loader, time/region selection, metadata and CRS resolution / 共用本地读取、时空裁剪与元数据处理 |
| `adapters/mlcast_sources.json` | Source catalog for sample preparation / 下载数据集信息 |
| `adapters/mlcast_*/` | Seven lightweight wrappers (`source.py` and their dependencies) / 七个轻量适配入口 |
| `contracts/data/mlcast_*.json` | Seven data contracts / 七个数据协议 |
| `scripts/download_mlcast_sample.py` | Download one local sample / 单组下载 |
| `scripts/mlcast_download_six.py` | Batch download and validation / 六组批量下载及验证 |
| `scripts/mlcast_make_request.py` | Generate YAML from a local sample / 根据本地样本生成 YAML |
| `scripts/mlcast_inspect.py` | DataBundle QA and full attributes / 质量检查与全部属性输出 |
| `tests/adapters/` | MLCast tests and catalog check / 新增回归测试与注册验证 |
| `README_MLCAST.md` | This consolidated guide / 本整合说明 |

**Unchanged / 保持不变：** `adapters/__main__.py`, `interfaces/data.py`, and the existing CMA/satellite loaders. The original `.vscode/extensions.json` is also **unchanged and not part of the MLCast changes**.

**Data folders / 数据目录：** `demo_data/mlcast/<dataset_id>/sample.zarr`, with one folder per dataset. Downloaded data and `.venv312` are local-only; do not commit them.

## 2. Download demo samples / 下载历史数据样例

Run from the NiB repository root in Windows PowerShell. All commands use the Python 3.12 virtual environment directly; activation is not required.

在 NiB 根目录的 PowerShell 中执行。直接使用 Python 3.12 虚拟环境里的解释器，不必激活。

**Italy / 意大利（单组示例）：**

```powershell
.\.venv312\Scripts\python.exe -m scripts.download_mlcast_sample --dataset it_dpc_sri_5min --output-root ./demo_data --download-steps 12 --history-steps 5 --size 256 --auto-time
```

**Remaining six / 其他六组批量下载并自动验收：**

```powershell
.\.venv312\Scripts\python.exe -m scripts.mlcast_download_six --output-root ./demo_data
```

**Retry one / 单组重试（法国示例）：**

```powershell
.\.venv312\Scripts\python.exe -m scripts.mlcast_download_six --output-root ./demo_data --only fr_mf_prate_5min
```

Outputs / 下载产物：`demo_data/mlcast/<dataset_id>/sample.zarr` plus `request.yaml`. Existing valid samples are reused unless overwrite is explicitly requested. / 默认复用已存在的样本，明确要求覆盖时才重新下载。

Seven IDs / 七组数据：`it_dpc_sri_5min`, `radklim_hourly`, `radklim_5_minutes`, `dmi_10_minutes`, `uk_metoffice_5min`, `be_rmi_radclim_mfb_5min`, `fr_mf_prate_5min`.

## 3. Run NiB Data Adapter / NiB 数据适配器运行指令

**Generate an editable YAML / 从已下载样本生成标准 YAML：**

```powershell
.\.venv312\Scripts\python.exe -m scripts.mlcast_make_request --dataset it_dpc_sri_5min --data-root ./demo_data --history-steps 3 --shift-steps 1 --crop-fraction 0.6
```

YAML is written to `tests/adapters/requests/mlcast_it_dpc_sri_5min/example_success.yaml`, following the original NiB example layout. It specifies the dataset, local path, time, history length, variable and geographic bbox.

标准配置与原 NiB 一样放在 `tests/adapters/requests/`，用于指定数据源、本地路径、时间、历史长度、变量及区域。

**Original dispatcher / 原有 NiB 总调度器：**

```powershell
.\.venv312\Scripts\python.exe -m adapters --config ./tests/adapters/requests/mlcast_it_dpc_sri_5min/example_success.yaml --data-root ./demo_data
```

Expected / 预期：`NiB Data Adapter Success`, `loaded times: 3`, `loaded fields: 3`.

**Boundary / 边界：** This command runs the **NiB Data Adapter locally**. The separate official NiB Docker/Hub demo can be started using `Start NiB.cmd`, but MLCast has not yet been wired into Hub model pairing or end-to-end inference. / 以上是 Data Adapter 本地调用；`Start NiB.cmd` 是原系统的 Docker/Hub 启动方式，但当前尚未在 Hub 完成 MLCast 与模型的配对和推理集成。

## 4. Downstream access / 下游任务获取数据指令

Use the existing `run()` API to obtain the in-memory `DataBundle` rather than parsing CLI text. / 下游应调用原有 `run()` 接口，直接获取内存中的 `DataBundle`，而不是解析终端输出。

**PowerShell:**

```powershell
.\.venv312\Scripts\python.exe -c "from adapters.__main__ import run; b=run('./tests/adapters/requests/mlcast_it_dpc_sri_5min/example_success.yaml','./demo_data'); t=sorted(b['data'])[-1]; c=next(iter(b['data'][t])); a=b['data'][t][c]; print('time:',t,'variable:',c,'shape:',a.shape,'units:',a.attrs.get('units'))"
```

**Python API:**

```python
from adapters.__main__ import run

bundle = run(
    "tests/adapters/requests/mlcast_it_dpc_sri_5min/example_success.yaml",
    "demo_data",
)
# Original NiB structure: source, request, data, source_files, metadata
last_time = sorted(bundle["data"])[-1]
channel = next(iter(bundle["data"][last_time]))
frame = bundle["data"][last_time][channel]  # xarray.DataArray
metadata = bundle["metadata"]
```

The data adapter preserves original units, projected grid, coordinates and metadata; it does not produce model-specific tensors. / 适配器保留原始单位、网格、坐标和元数据；不负责模型专用张量转换。

## 5. Full data attributes / 数据全属性输出指令

**Quality summary / 质量摘要（时次、空间分辨率、bbox、有效比例、min/max）：**

```powershell
.\.venv312\Scripts\python.exe -m scripts.mlcast_inspect --config ./tests/adapters/requests/mlcast_it_dpc_sri_5min/example_success.yaml --data-root ./demo_data
```

**All attributes / 全属性（DataBundle metadata、变量和坐标 attrs/encoding）：**

```powershell
.\.venv312\Scripts\python.exe -m scripts.mlcast_inspect --config ./tests/adapters/requests/mlcast_it_dpc_sri_5min/example_success.yaml --data-root ./demo_data --all-attrs
```

`--all-attrs` prints metadata and variable/coordinate attributes, **not all pixel values**. / `--all-attrs` 输出元数据及变量、坐标的所有属性，**不打印整个像素矩阵**。

To inspect a different dataset, change its dataset ID in the YAML-generation step and the `mlcast_<dataset_id>` config path. / 其他数据集只需要替换数据集 ID 与对应 YAML 路径。

## 6. Python environment notes / Python 环境注意事项

**Required for the tested local workflow / 当前本地测试环境：Python 3.12 + Zarr 3.x.** The local demo files are stored in **Zarr V2 format**, which Zarr 3 can read; remote inputs may use V2 or V3. / 本地样本保存为 **Zarr V2**，但使用 **Zarr 3** 库进行读取；远端来源可能是 V2 或 V3。

```powershell
py -3.12 -m venv .venv312
.\.venv312\Scripts\python.exe -m pip install -r ./scripts/mlcast_py312_requirements.txt
.\.venv312\Scripts\python.exe -m pip check
```

Use ` .\.venv312\Scripts\python.exe ` instead of plain `python` so the commands do not accidentally run under Python 3.9. / 不要直接写 `python`，避免调用到原有 Python 3.9。

**Status / 验收状态：** Seven datasets passed local real-sample download, YAML selection, DataBundle readback and QA in the user's Windows environment. The original NiB dispatcher and DataBundle contract remain unchanged. / 七组数据已在 Windows 环境完成真实样本下载、YAML 选取、DataBundle 读取与 QA 验收，原有调度器及数据接口保持不变。
