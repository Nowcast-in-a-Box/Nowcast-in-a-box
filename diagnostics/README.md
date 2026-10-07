# Diagnostics 镜像

该目录下包含所有的 `visualization` 和 `metrics computation` 实现代码。

## 构建

在仓库根目录：

```sh
docker build -t nib-diagnostics:dev diagnostics/
```

第三方包加在 `requirements.txt` 文件中，**版本固定**。

## 入口

暂未实现，预期的调用形式：

```text
python -m diagnostics --config /run/request.yaml
```

`request.yaml` 文件包含：

- 需要计算的指标名称
- 预报结果、观测值（Ground truth）

## 存储

对持久化存储服务，请用 SQLite，文件放在每个 run 的文件夹内，如 `/run/products/diagnostics.sqlite`。

## 请求文件

示例文件： `contracts/examples/diagnostics-request.example.yaml`。

```yaml
observation: /run/inputs/observation
forecasts:
  - id: wadepre
    path: /run/inputs/forecasts/wadepre
metrics:
  - family: continuous
    items: [mae, rmse]
  - family: yes_or_no
    items: [csi]
    levels: [35]
  - family: spatial
    items: [fss]
    levels: [35]
    half_windows: [2, 4]
```

## 运行结果/过程判定依据

- 程序运行过程中没有任何报错，包括 warnings, errors。
- `/run/products/metrics.json` 非空，并且包含必需的字段内容，且值合理。
- `/run/products/frames/` 目录下包含逐帧的可视化结果。

上述标准全部符合，认定这次评估结果顺利完成
