# 我们请求上游提供的数据读取接口

> 给 Nowcast-in-a-Box 维护者。中文。
> 相关：`interfaces/data.py`（现有的 `DataAdapter`）· `diagnostics/README.md`（diagnostics 请求）

## 一句话

**我们想按「名字 + 时间 + 时效」取一个场，别的都不想管。**

```python
truth   = source.get_observation(name, valid_time)              # 名字 + 时间
predict = source.get_prediction(model_id, init_time, lead)      # 名字 + 时间 + 时效
```

`Field` 自己带坐标、带单位、带有效时刻。**不要告诉我们文件在哪、什么格式、用不用 xarray。**

---

## 一、为什么现在这样不行

现有的 `interfaces/data.py` 是**给模型喂数据**设计的：一次 `load(request)` 返回一整批（context 帧 + target 帧），
因为 HDF5 打开一次、定位一行、把该读的都读出来，这样最划算。**这个设计我们认同，也建议保留。**

但是**做检验的人不需要那一批**。我们要的是：

> 「2017-06-27T00:35Z 的实况，第 40 分钟那个预报，各给我一个二维场。」

现在要拿到这一个场，调用方得先知道并凑齐：

```
source / data_dir / data_root / initial_time / history_steps /
interval_minutes / channels / bbox          ← 8 个字段
+ ChannelSpec.mode / SpatialSpec.crop / bbox_order / native_cadence_minutes
```

**这些全是"读取器内部的事"**，不是"我要哪个场"这件事的一部分。
`history_steps` 和 `interval_minutes` 尤其别扭：它们是**模型要几个帧、隔多久**，
而做检验的人只想知道**某一个时刻的实况**。

---

## 二、我们想要什么（5 个方法）

```python
class FieldAccess(Protocol):

    def capabilities(self) -> Capabilities:
        """声明：步长、有哪些实况、有哪些模型、单位。不开文件。"""

    def list_observations(self, name: str, t0, t1) -> list[datetime]:
        """[t0, t1] 里，name 确实有数据的、按步长对齐的时刻。"""

    def get_observation(self, name: str, valid_time) -> Field:
        """某一个确切时刻的实况场。"""

    def list_predictions(self, model_id: str, init_time) -> list[int]:
        """这次起报，模型**实际产出了**哪些时效（分钟）。"""

    def get_prediction(self, model_id: str, init_time, lead_minutes: int) -> Field:
        """某一个起报时刻 + 某一个时效的预报场。"""
```

**返回的 `Field`：**

```python
Field(
    data,        # 二维数组
    coords,      # 自己的网格（一维 lon / lat，严格递增）
    valid_time,  # 有效时刻（带时区）
    unit,        # 单位字符串
    variable,
    name,
    mask,        # 缺测标记；**没有就是 None，不用 NaN 代替**
)
```

---

## 三、三条硬要求

**① 场必须自己带坐标**

不要让我们从「请求时给的那个 bbox」反推坐标 —— 那是**我们的请求**，不是**数据的声明**。
现在 `DataBundle` 里没有坐标（只有 `data / source_files / metadata`），
而 `Forecast` 里有 `coords` —— **这个不对称应该消掉，两边都带。**

**② 场必须自己带单位**

现在单位只在 `DataCapabilities.channels.variables` 里，而且 `mode = "open"` 时 `variables` 是空的，
**等于 `open` 的源没有单位可给**。做检验必须知道单位：单位不一致的预报和实况比出来的分数没有意义。

**③ 没有数据要明确报错，不要返回空数组**

```
要的东西不存在   →  抛异常（我们记成"这次运行没有产出"）
存在的但是缺测   →  在 mask 里标出来，**不要填 0、也不要填 NaN**
```

我们区分「**缺口**」和「**0**」。把缺口写成 0，图上看不出区别，但结论已经错了 ——
这是这个模块最在意的一条。

---

## 四、一条性能要求（很重要，容易做反）

**同一个「来源 + 起报时刻」，只允许读一次盘。**

如果按时刻逐个取就去开一次文件，那比现在还慢。所以：

```
接口是"一个时刻一个场"  ←  外面看到的样子
底下是"一次读一批"      ←  DataAdapter.load() 保留
中间必须有缓存          ←  且是**实现方的责任**，不是调用方的
```

**缓存放在接口实现里面**，不要要求调用方先 `load` 再一个个取 —— 那等于没简化。

---

## 五、明说不要的东西

| 不要 | 为什么 |
|---|---|
| 让我们知道文件路径、文件名、目录结构 | 那是读取器的事 |
| 让我们知道文件格式（HDF5 / NetCDF / …） | 同上 |
| 要求我们用 xarray | 我们的核心只依赖 numpy |
| 要求我们传 `history_steps` / `interval_minutes` 才能取一个实况 | 这两个是模型的需要，不是检验的需要 |
| 用 NaN 代替"没有这个数据" | 见三③ |

---

## 六、这一改，顺手解决了另一个悬着的问题

我们查过：**预报存在哪、什么格式，上游没有任何地方写。**
`predict()` 返回内存里的 `Forecast` 就结束了，而 `diagnostics` 请求里却有
`forecasts: [{id, path}]` —— 说明平台确实会落盘，但那个约定没有写下来。

**如果按上面的接口来，这个问题就不用回答了**：

```
get_prediction(model_id, init_time, lead) -> Field
                                            ↑ 存储格式变成实现方的内部事
```

我们不需要知道预报放在哪、什么格式 —— 就像我们不需要知道 HDF5 的行列布局一样。

---

## 七、过渡安排（避免两份实现长期并存）

| | |
|---|---|
| **建议** | 接口放在 `interfaces/` 里，和 `data.py` 并列，由上游实现 |
| **理由** | 指标计算和可视化**两个消费者都要用**；各写一份就是同一个东西两份实现，那是静默失败藏身的地方 |
| **过渡** | 在接口做好之前，**我们自己先实现一版**（对着 `DataAdapter` 适配，带缓存），这样不被阻塞 |
| **要明确说清** | **这是过渡**。上游的做好了，我们把 provider 换掉（一个分支的事），不是两套永久并存 |

---

## 八、一个最小验收

接口做好之后，下面这段应当能跑，而调用方**不该出现任何文件名、格式名、xarray**：

```python
source = <上游给的实现>
truth = source.get_observation("sevir", datetime(2017, 6, 27, 0, 40, tzinfo=utc))
assert truth.unit == "raw"
assert truth.coords.shape == truth.data.shape
assert truth.missing.dtype == bool

leads = source.list_predictions("wadepre", datetime(2017, 6, 27, 0, 35, tzinfo=utc))
predict = source.get_prediction("wadepre", datetime(2017, 6, 27, 0, 35, tzinfo=utc), leads[-1])
assert predict.unit == truth.unit          # 单位必须一致，否则我们的核心会拒绝
```

**再补一条性能验收**：连取同一个起报时刻的 20 个时效，
**文件只被打开一次**（实现方应当能报出这个计数）。
