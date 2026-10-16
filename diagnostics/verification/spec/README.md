# spec/ 索引

这个目录里每份文件是给谁看的、解决什么。

## 发给上游的（英文，可直接复制）

| 文件 | 给谁 | 一句话 |
|---|---|---|
| `data-access-we-ask-for.en.md` | 上游维护者 | **运行程序**要的：按「名字 + 时间 + 时效」取一个场；场自带坐标和单位；**没数据要报错不要返回空**；**同一个起报时刻只读一次盘** |
| `interfaces-we-consume.md` | 上游维护者 | **我们的位置变了**：不再产生配置，只消费。四个接口 —— `list_groups` / `get_group`（字典）/ `read_truth` / `read_forecast`（xarray）|
| `what-the-creator-asks-for.en.md` | 上游维护者 | **创建程序**要的：声明必须完整机读可枚举（缺**范围与分辨率**、缺**运行产出布局**、`open` 源的变量单位缺失） |

中文原件（`*-we-ask-for.md` / `what-the-creator-asks-for.md`）保留 —— 那是推理时用的语言。
**译文和原文漂移比只有一份更糟。**

## 我们自己看的

| 文件 | 解决什么 |
|---|---|
| `upstream-alignment.md` | **上游现在长什么样**：目录分工、`interfaces/` 契约、一个真实实现的结构、**三个发现**（真值在 `metadata["target"]` 里 / `units="raw"` / adapter 不重采样）、映射表、五步计划、已定与未定 |
| `step-0-move.md` | **第 0 步的记录（已完成）**：合并零冲突 · 纯搬家 0 插入 0 删除 · 699 例 `EXIT=0`；另附这一轮踩过的工具教训 |
| `step-1-upstream-access.md` | **第 1 步的落地草稿**（还没写代码）：`access/upstream.py` 怎么填、**我上次想错的地方**、三个"bundle 里没有"的东西、异常映射、缓存、8 个测试 |

## 更早的（设计阶段的规格，仍然有效）

| 文件 | 内容 |
|---|---|
| `verification-config.md` | 配置的规格：尺子（不可变）/ 名单（可变）/ 指标（只增），三条不变量 |
| `field-access.md` | `FieldAccess` 契约的散文规格 + 提供方要满足的**五项保证** |
| `field-access-http.md` | 通过 HTTP 说这个契约的规格（客户端的透明性） |
| `catalog.md` | 编目与匹配；运行 provenance 的开放问题 |

## 读的顺序

**刚接手** → `upstream-alignment.md`（上游有什么）→ `verification-config.md`（我们怎么设计）
**要动手** → `step-1-upstream-access.md`（照着填）
**要发上游** → 上面那两份 `.en.md`
**想知道走到哪了** → `step-0-move.md`
