# 第 0 步：切分支，搬到 `diagnostics/verification/`

> 计划，**还没执行**。执行时照这个顺序做，每步都要能看到结果。
> 前置阅读：`spec/upstream-alignment.md`

## 为什么这一步必须在 ① 之前

`access/upstream.py` 要 `import interfaces.data`。而 **`interfaces/` 只在 `main` 上**：

| 文件 | `feat/diagnostics` | `main` |
|---|---|---|
| `interfaces/data.py` | ❌ **没有** | ✅ |
| `interfaces/model.py` | ❌ 没有 | ✅ |
| `adapters/catalog.json` | ❌ 没有 | ✅ |
| `adapters/sevir/source.py` | ❌ 没有 | ✅ |
| `utils/paths.py` | ❌ 没有 | ✅ |

`feat/diagnostics` 从 `c0d622b`（#5 数据适配器）分出来，**漏了 `d252802`（#7 模型适配器）** ——
而 `interfaces/` 正是在 #7 里进来的。

**`feat/diagnostics` 比 `main` 只落后 1 个提交**，所以合起来很容易。但**不合就动不了**。

---

## 0.0 前置：等管理员更新 `feat/diagnostics`

**已请人更新（2026-10-10）。** 如果他们把 `main` 合进去了，下面 §0.1 的合并就不需要做了。

### 要问的四条

1. **把 main 合进 `feat/diagnostics`** —— 分支上现在没有 `interfaces/` / `adapters/` / `utils/`
   （从 `c0d622b` 分出去，漏了 `d252802`，只落后 1 个提交）。我们不 `import interfaces.data` 就写不下去。
   合并或 rebase 都可以 —— **我们还没基于它开分支**。
2. **存储粒度**：`diagnostics/README.md` 写的是每次调用一个库（`/run/products/diagnostics.sqlite`）；
   我们是一个工作区一个库、按配置分区。最终以哪个为准？
3. `contracts/README.md` 说 diagnostics request「**不是** evaluation draft」—— 两者什么关系？
4. **`diagnostics` 入口**（`python -m diagnostics --config /run/request.yaml`）写着「暂未实现」——
   **是不是可以由我们来填？**

### 基线选谁，决定了 PR 提给谁 —— 这是硬约束

| 基线 | PR 目标 | 结果 |
|---|---|---|
| `main` | `main` | ✅ 干净、独立、不依赖他们什么时候完成 |
| `feat/diagnostics`（已含 main） | `feat/diagnostics` | ✅ 干净，但挂在别人未合并的分支上 |
| `main` | `feat/diagnostics` | ❌ **diff 被污染** —— 那个分支少的 76 个文件会显示成"我们新增的" |
| `feat/diagnostics`（未含 main） | 任意 | ❌ 没有 `interfaces/`，写不出第 ① 步 |

**所以：要么等他们更新完、以它为基线、PR 也提给它；要么现在就换 `main` 为基线、PR 提给 `main`（两边路径不同，不冲突）。**

### 顺带：`black.toml`

上游用 **black** 格式化。我们的代码不一定符合 ——
**提 PR 之前要跑一次**，否则 review 里全是纯格式 diff。

---

## 0.1 把 main 合进来（**若 §0.0 已由管理员完成，本步跳过**）

```bash
cd D:/mins_root_workspace/nowcast-zhou/Nowcast-in-a-Box
git config http.proxy http://127.0.0.1:7897     # 这台机器要经代理才能到 GitHub
git fetch origin main feat/diagnostics

# 从上游的 diagnostics 分支开一条我们的分支
git switch -c feat/diagnostics-verify origin/feat/diagnostics

# 把 main 合进来（只差 1 个提交，应当无冲突）
git merge origin/main -m "merge main into feat/diagnostics (interfaces/, adapters/, utils/)"
```

**验收**：下面六个文件全部存在

```bash
for f in interfaces/data.py interfaces/model.py adapters/catalog.json \
         adapters/sevir/source.py adapters/sevir/wadepre/adapter.py utils/paths.py; do
  test -f "$f" && echo "  OK   $f" || echo "  缺   $f"
done
```

**如果这个合并有冲突 —— 停下来问，不要硬解。**

## 0.2 把代码搬过去（`git mv`，不要删了重加）

目标：`review/verify/` → `diagnostics/verification/`，和 `diagnostics/visualization/` 并列。

```bash
mkdir -p diagnostics/verification
git mv review/verify/nib_verify     diagnostics/verification/nib_verify
git mv review/verify/tests          diagnostics/verification/tests
git mv review/verify/spec           diagnostics/verification/spec
git mv review/verify/pyproject.toml diagnostics/verification/pyproject.toml
git mv review/verify/README.md      diagnostics/verification/README.md
git mv review/verify/DEMO.md        diagnostics/verification/DEMO.md
git mv review/verify/.gitignore     diagnostics/verification/.gitignore
```

**不搬**（本机运行状态，已 gitignore，本来就不在 git 里）：
`_svc/`、`_shot/`、`_tmp/`、`*.sqlite`
**留在原地**：`review/`（现在可能只剩空目录 —— 检查一下还有没有别人要的东西）

**一个可以以后再做的小决定**：`visualization/` 用 `src/nib_visualization/` 布局，
我们若要对齐得改成 `src/nib_verify/` —— **第 0 步先不做**，那会牵动 `pyproject.toml` 的包发现。
**先搬位置，后调布局**，两件事不要挤在一个提交里。

## 0.3 找出所有写死的路径

搬之前先查，搬之后要保证没有漏的：

```bash
grep -rn "review/verify\|review\\\\verify\|review\.verify" \
     diagnostics/verification --include=*.py --include=*.toml \
     --include=*.md --include=*.html --include=*.js \
  | grep -v "spec/step-0-move.md"
```

**已知要看的几处**：

| 在哪 | 为什么 |
|---|---|
| `pyproject.toml` | 包发现、`testpaths` |
| `nib_verify/server/app.py` | `STATIC_DIR` 的推导（通常是 `__file__` 相对，搬了会自动对） |
| `demo.py` | 默认工作区路径 |
| `README.md` / `DEMO.md` | 文档里的命令 |
| `.gitignore` | 里面的相对路径 |
| `_nib_repo/` 里的交付文档 | 工作区根目录，不在这个仓里，另外处理 |

## 0.4 跑全套，确认没搬坏

```bash
cd diagnostics/verification
python -m pytest -q                    # 期望 699 passed
python -m pytest --collect-only 2>&1 | grep -cE "^tests/.*::"   # 期望 699
```

**再确认服务还能起**（它读的是仓库外的 `nib-demo`，路径不该受影响）：

```bash
B="D:/mins_root_workspace/nowcast-zhou/nib-demo"
python -m nib_verify serve --root "$B/.store" --configs "$B/configs" \
  --packages "$B/packages" --runs "$B/workspace/jobs" \
  --host-config "$B/config.yaml" --port 8766 --log-level warning --access mock
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8766/
```

## 0.5 分三次提交，不要一次巨型提交

| 提交 | 内容 |
|---|---|
| 1 | merge main（§0.1） |
| 2 | `git mv` 纯搬家（**内容零改动**） |
| 3 | 修 `pyproject.toml` / 文档里的路径 |

**第 2 次提交必须是纯移动** —— `git show --stat` 应当只显示 `rename`，`git diff -M` 应当是空的。
**混进内容改动的搬家，事后谁也说不清是搬错了还是改错了。**

---

## 搬完之后的顺序

```
第 1 步  diagnostics/verification/nib_verify/access/upstream.py
         （在 DataAdapter 上实现 FieldAccess；核心链路 0 改动）
第 2 步  planner 构造 DataRequest
第 3 步  清单改读 adapters/catalog.json + contracts/data/*.json
第 4 步  抄 utils/paths.py 的路径守卫 + channels 的 open/closed
第 5 步  实现 diagnostics 入口（上游写着"暂未实现"）
第 6 步  补 contracts/examples/diagnostics-request.example.yaml
```

---

## 这次踩过的两个工具教训（搬完之前先立住）

1. **改动只走会报错的工具**（`edit` / `write`）。
   这个会话里脚本里的 `str.replace` **静默失败了至少三次**（`SEGMENTS` 的像素宽度、`INIT_PICKS` 的整批回滚、`_x_ticks`）。
2. **每次替换都要打印"替换了几行"**，否则它失败了也会说 `ok`。
3. **看输出要看全部输出**：`tail -3` 截掉过 traceback（让我把三次失败读成三次成功），
   SQLite 基准两次读到假数据（年份错、`strftime` 少了 `'unixepoch'`）。**测量脚本要先断言"这是我想要的数据"。**

---

## 还没定的（搬完也不影响）

- **存储粒度**：一工作区一库（现在）/ 一调用一库（上游写的 `/run/products/diagnostics.sqlite`）
- `contracts/README.md` 说 diagnostics request「**不是** evaluation draft」—— 关系要问清
- `docs/modules.yaml` 里 D1 仍是 `status: planned` / `owner: null`

## 只能你做的

- **点 Fork**：`https://github.com/Nowcast-in-a-Box/Nowcast-in-a-box` → Fork
  （`liucouhua/Nowcast-in-a-box` 现在还是 404）
