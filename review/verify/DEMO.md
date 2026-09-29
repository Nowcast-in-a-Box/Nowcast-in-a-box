# 演示脚本：一条命令，从"还没有平台"到一张图

给项目组的演示。全部内容都在 `review/verify/` 里，不依赖平台、不依赖真实数据、
不依赖网络。**唯一虚构的是"平台那一侧"**，而且虚构得明明白白——不是藏起来装作
真的。

> 演示前：`pip install -e '.[web]'`（页面和取场服务需要 fastapi/uvicorn）。

---

## 第 0 步 · 开场白（30 秒）

> D1 检验评估模块。现在还没有真实平台、没有真实数据、没有模型，所以我们的做法是：
> 把数据接口定义成一份契约，我们自己造一个**模拟的数据服务**来实现它，整条链——
> 从平台的配置文件到最后的图表——现在就能跑通、能测、能演示。将来平台的真服务
> 上线，**只换一个 URL**。

---

## 第 1 步 · 一条命令（10 秒）

```bash
cd review/verify
python -m nib_verify demo --open
```

屏幕上会出现六段，边跑边念：

```
  1/6  platform     fabricating a workspace in ...\nib-demo
         config.yaml         review 127.0.0.1:8766, data root inputs/data
         packages            6 manifests (2 products, 4 models), in unrelated directories
         radar-composite    vil [kg m-2]
         satellite-rain     rain_rate [mm h-1]
         run configs         24 runs (6 init times x 4 model/source pairs)

  2/6  catalog      read by content, not by layout
         data packages       2   satellite-rain, radar-composite
         model packages      4   mlcast-det, satcast-det, mlcast-alt, satcast-alt
         compatible pairs    4 of 8 pass the Matcher   <- 跨单位的 4 对被拒
         runs                24, mlcast-det, satcast-det, mlcast-alt, satcast-alt

  3/6  config       2 configs, one per product        <- 两套参数几乎不共享
         vil-demo   ruler c1fa754e1f3939f8  radar-composite [kg m-2] @ 0.25
                    2 members mlcast-det/mlcast-alt
                    leads [10, 20, 30, 60]   levels [16, 74, 133, 181, 219]   window (1, 2)
                    4 metric families: continuous, moments, spatial, yes_or_no
         rain-demo  ruler 0db36ae64e983d13  satellite-rain [mm h-1] @ 0.5
                    2 members satcast-det/satcast-alt
                    leads [30, 60, 90, 120]  levels [0.5, 2, 5, 12]          window (2, 4)
                    4 metric families: continuous, moments, spatial, yes_or_no

  4/6  datafeed     serving the 'convective' scenario
         10-minute cadence, observations radar-composite [kg m-2],
         satellite-rain [mm h-1], 4 models
         http://127.0.0.1:7834   <- a real service on a real socket

  5/6  verify       through that service, over HTTP -- not in process
         vil-demo    planned  48 tasks over 6 init time(s)
                     ran      40 done, 8 unavailable, 0 failed, 0 skipped
                     fetches  48 observation, 48 prediction, over the socket
         rain-demo   planned  48 tasks over 6 init time(s)
                     ran      40 done, 8 unavailable, 0 failed, 0 skipped
                     fetches  48 observation, 48 prediction, over the socket

         vil-demo: CSI at 133 kg m-2, by lead time
                10     20     30     60
         mlcast-alt   0.866  0.833  0.840  0.860
         mlcast-det   0.879  0.879  0.889  0.920

         rain-demo: CSI at 5 mm h-1, by lead time
                30     60     90    120
         satcast-alt  0.352  0.341  0.326  0.309
         satcast-det  0.390  0.377  0.367  0.349

  6/6  review       http://127.0.0.1:8766/
```

浏览器会自动打开页面。每个数字对应的"话术"见下表。

| 段 | 台上怎么说 |
|---|---|
| 1/6 | "平台这一侧是我们**虚构**的，目录故意摆得乱七八糟——因为发现包靠的是**内容**（有 `offers` 是数据包，有 `requires` 是模型包），不靠目录长什么样。将来指到真平台，读的是它自己的东西。" |
| 2/6 | "Matcher 检查数据包和模型包声明的东西——单位、网格、帧数。2 对 2 全过。" |
| 3/6 | "一条检验配置 = 一把尺子 + 一张名单 + 一组指标。尺子不可改——改了指标的分母和掩膜就变了，读数不再可比。名单可以增删，指标只增不改。三条规则由**服务端**强制，浏览器绕不过去。" |
| 4/6 | "这不是进程内桩子，是**真 socket 上的真服务**。你可以在另一个终端 `curl http://127.0.0.1:49141/capabilities` 试试。将来平台的数据服务就长这样。" |
| 5/6 | "注意这一行：`through that service, over HTTP -- not in process`。**客户端走的是和将来完全一样的 HTTP 路径**。48 个任务：40 完成、8 不可用——不可用是模拟数据故意缺的（每 5 次起报缺 1 次），不是我们的错误，是可查的。`8 unavailable` 就是"某个起报没产出"这种真实情况。" |
| 6/6 | "最后是审查页。" |

---

## 第 2 步 · 浏览器里演示（5 分钟）

### Results 页签

1. **Config** 选 `vil-demo`，**Expansions** 点 `member` 和 `lead_minutes`，**Run query**。
   - 表格出来，图自动出现在下面。
   - 说："图表是**同一个查询的另一种看法**——数据和表格走同一条路径，不可能跟表格对不上。最后一个展开维度是横轴，前面的分线，这是查询层早就写好的约定，页面只是读回来。"

2. **Levels** 填 `133`，再点 Run query。
   - 数字变了，没有任何重算。
   - 说："这就是 D1 的验收条件——**改阈值不重跑推理**。`tests/test_done_when.py` 里五条测试钉着它：阈值互相独立、查询永不碰数据层、重跑已完成期次一次读取都没有、5 个阈值和 2 个阈值的预测读取次数一样。"

3. 把 **Levels** 清空，点 Run query 看全表；把 `member` 从展开里取消，看单条线。

### Configs 页签

4. 打开 `vil-demo`。
   - 说："尺子（实况、空间、时间步长）在页面上**是锁死的**——编辑被服务端拒绝，不是前端不让你改。指标可以加。你可以在菜单里挑一个没算过的阈值查——直接从库里出来，不用重跑。"

### New config 页签

5. 走一遍向导：Identity → The ruler → The roster → The metrics → Preview。
   - 说："新建配置时数据包/模型包的候选来自目录，是平台给什么我们用什么。空间范围现在手填，将来自动发现。"

### 台下如果问"数据是假的吧？"

> 数据是模拟的，但**这段链是真的**：配置校验、匹配、调度、取场（HTTP）、重采样、
> 单位守卫、缺测处理、可加量存储、查询、绘图——全都是将来跑真数据的同一份代码。
> 区别只在最上面一层 provider，将来换真服务只改一个 URL。
>
> 而且模拟数据不是拍脑袋的：`--scenario convective` 是多单体平流、模式间有技能
> 差、有雷达式覆盖缺口。CSI 随阈值从 0.93 掉到 0.67——**是可以出错、看得出对错的
> 数字**，不是全 1.0。

---

## 第 3 步 · 想深挖时的三个终端命令（都在另开一个终端跑）

```bash
# 数据服务是活的：
curl http://127.0.0.1:49141/capabilities
curl http://127.0.0.1:49141/observations/radar-composite/2026-01-01T00:00:00Z

# 平台配置是模块自己读的：
python -m nib_verify host show --host-config nib-demo/config.yaml

# 规划只表态意图，不表态事实：
python -m nib_verify task plan --config nib-demo/configs/vil-demo.json \
    --runs nib-demo/workspace/jobs --probe
```

---

## 重置

```bash
# 重建整个演示（覆盖）：
python -m nib_verify demo --where nib-demo --force --open

# 换个天气：simple（完美预报）/ widespread（层状）/ patchy（大量缺测和缺起报）
python -m nib_verify demo --where nib-demo --force --scenario patchy --open

# 只到"验证完"为止，不起页面（适合先排练）：
python -m nib_verify demo --where nib-demo --no-serve
```

---

## 五分钟以外的花絮（台下问得到）

| 问题 | 答案 |
|---|---|
| 单位不对怎么办？ | `runner` 有单位守卫：provider 返回 dBZ 而尺子声明 kg m-2 就直接失败——**阈值按错误单位算出来毫无意义**。 |
| 缺测怎么办？ | 缺测必须是 NaN，不许填 0。填 0 会变成"正确否定"，把每个分类评分都抬高。解码器对"说没缺测但带 NaN"的载荷会把它并入掩膜并记一条警告。 |
| 数据量？ | 每年 78.8 万行（单成员）。加指标**不增行**，只有加等级才增行。语法：单年 0.2–0.5 秒。 |
| 和 MetEva 什么关系？ | 中间量结构对齐：`H/F/M/C`、`T/E/A/S`、`T/MX/MY/SX/SY/SXY`、`pob/pfo/fbs`；`mps.score_df` 的 `(数值, 维度描述)` 对应这里的 `records + dims`。 |
| 为什么图表是服务端 SVG？ | 浏览器里画会有第二个渲染器，两个渲染器会漂；图也导不出来。SVG 是文本，能打印、能贴 PPT、测试能读。 |
| 测试规模？ | **512 例全绿**，无 GPU、无网络、无真实数据。浏览器测试用真 Edge 跑一遍页面（0 console error）。 |
