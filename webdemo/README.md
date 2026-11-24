# MewCode · Coding Workspace 与 RSI Center

默认页面打开普通 Coding 会话。点击「选择本地目录」浏览文件夹或粘贴完整路径，也可以先写消息再选目录。普通目录可以直接提问、授权编辑、运行测试并查看终端输出。打开 Git 工作树后还可查看差异。左侧的 RSI Center 打开实验、评分与 Harness 版本页面，已有的 `#workspace/<job_id>` 链接仍可打开历史作业。

## 安装与启动

在 MewCode 源码根目录安装 Python 和 Web 依赖。本机启动需要 Git、uv、Node 与 npm。普通 Coding 的 Shell 已在 macOS ARM64 登录会话验收，使用系统沙箱及 launchd 监督写进程。

```sh
uv sync --frozen
uv pip install --python .venv/bin/python -r webdemo/requirements-dev.txt
npm --prefix webdemo/frontend ci
```

在两个终端分别启动。仅运行服务时可将上面的 Python 依赖文件换成 `webdemo/requirements.txt`。

```sh
webdemo/start-backend.sh
```

```sh
webdemo/start-frontend.sh
```

后端监听 8765。前端默认打开 Coding Workspace，选择 RSI 演示模型时才启动本机 Mock 服务。两个启动脚本均以前台进程运行，用 Ctrl-C 停止。[本地工作台](http://127.0.0.1:5173) · [接口文档](http://127.0.0.1:8765/docs)

运行独立 RSI 评分时启动 Docker，并取得冻结的评分镜像。

```sh
docker pull --platform linux/arm64 python@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
```

单独构建前端可执行以下命令。

```sh
npm --prefix webdemo/frontend run build
```

### 本地配置

| 配置项 | 默认值或用途 |
| --- | --- |
| `RSI_WEB_HOME` | `~/Library/Application Support/MewCode/WebConsole`。保存 Web 任务日志、注册仓库和新实验，须位于受保护的引擎源码目录之外 |
| `RSI_ARCHIVE` | `~/Library/Application Support/MewCode/RSI`。读取已有的 Day 2–5 归档；缺少归档时可直接创建新实验 |
| `RSI_PUBLIC_REPOS` | Web 数据目录下的 `public-repos/`，供操作者发布可导入的公开 Git 项目 |
| `CODING_WEB_HOME` | 普通会话 SQLite 库、快照与工具输出。默认位于仓库外的 `~/Library/Application Support/MewCode/CodingWorkspace` |
| `CODING_PROVIDER_CONFIG` | 可指定本机 MewCode 配置文件。缺省时使用原生配置加载顺序及首个 Provider |
| `RSI_PROVIDER_CONFIG` | 默认读取引擎的 `.mewcode/config.yaml` |
| `RSI_PORT`、`RSI_UI_PORT` | 后端 8765、前端 5173 |
| `RSI_API` | Vite 的 API 代理地址，默认 `http://127.0.0.1:8765`。改后端端口时同时设置 |
| `RSI_MOCK_PORT` | 18963。继续既有 Mock 谱系时保持原端口，模型端点是引擎指纹的一部分 |

**无密钥演示**使用内置 `demo-mock`。它通过本地模型协议为分页示例返回确定性工具调用，也为两轮 Default RSI 实验生成可验证的策略提案。实际模型请求、原生 Agent Loop、工具权限、工作树、Docker、评分和版本决策全部执行。演示模型对其他公开仓库仅做有限的检查。

**真实模型**沿用 MewCode 的 Provider 配置，可参照 `.mewcode/config.yaml.example`，在服务端私有配置填写 `name`、`protocol`、`base_url`、`model` 和服务端凭据。也可使用该协议在 MewCode 中支持的环境变量。前端下拉框只读取来源类型、Provider 名称和模型名。选择 Real 会向相应服务发起模型请求并可能产生费用。独立 Evaluator 仅继承 Docker 所需环境。

## 三个演示流程

### A · 运行一个仓库任务

1. 进入 Coding workspace，选择 `page-settings`。输入“显式 limit 优先于 settings.page_size，0 返回空页”，或点 **Use benchmark task** 装载完整题面。
2. 选择模型、Harness、轮数和超时，点 **Run task**。左侧显示该仓库文件，中央显示模型请求、工具调用、权限、结果和结束事件。
3. 等待独立评分，在右侧切换 **Diff / Files / Tools**。**Replay score** 从相同封存产物重新评分，逐字段比较原回执。页面刷新后可以继续查看结果。
4. 核对评分时注意题面范围。冻结题目的分数来自原题的独立 Evaluator。额外输入会作为本次任务专属的指令叠加到固定快照副本，原 Harness 摘要和有效策略摘要分别保存在启动配置与评分回执中。工作台单题运行不参与正式版本晋升。

### 注册自己的公开仓库

以下脚本从随项目提供的公开分页题创建一个已提交的演示 Git 项目。

```sh
webdemo/demo-repository.sh
```

在 Coding workspace 的 Repository 面板点 **+**，输入 `pagination-demo` 并注册。也可把自己的公开 Python Git 项目作为 `RSI_PUBLIC_REPOS` 的直接子目录，提交所需文件及顶层 `public_checks.py` 后用目录名注册。相同的名称和 HEAD 会返回已有记录。

导入器只从指定提交读取允许格式的常规文件，生成任务独享的快照；隐藏文件和常见凭据文件会被排除。请仅发布适合交给所选模型的项目。单文件上限 64 KiB，合计 1 MiB，支持 Python、Markdown、文本、TOML、JSON 和 YAML。注册仓库复用 MewCode 工具和只读容器，完成后将封存产物与**导入时的公开测试文件**组合再测试。页面显示公开测试通过或失败。正式 0–100 分要求冻结题目和独立评分规范。

### B · 运行一次 Default RSI

1. 在 Evolution center 创建新实验，选择 `repo-python-v1`、Mock、`v1 · new lineage`、2 轮、每题 1 次、目标 100。
2. 后端执行三题正式基线。界面可查看 Trial、Evaluate、Reflect、Improve、Validate、Decision，生命周期日志支持断线补读。
3. 从 **Failure analysis** 打开证据轨迹。在 **Strategy proposal** 检查假设和修改的 Prompt，在 **Validation & decision** 对照逐题分数和候选版本。
4. 脚本模型演示的第一次候选提高分页分数，第二次候选回落。VersionManager 会保留获胜的父版本。Real 模式的结果由真实模型及裁判决定，达到目标即可结束，轮数是上限。
5. `Starting harness` 可选 Web 创建的活动谱系。继续谱系时须选择原被测 Provider。Web 提交时锁定起始引用，旧归档只读。同一谱系的实际晋升仍由原引擎的 SQLite 事务决定。

### C · 查证历史结果

在 Evolution history 打开一项旧实验，按轮查看报告。在 **Inspect trajectory** 打开单题轨迹和代码产物，在 **Snapshot** 查看文件和父版本差异。Evaluations 只允许在同一条谱系中比较版本。Harness versions 显示父引用、决策、活动状态及时间。引擎在拒绝时保留原引用，当前治理接口没有手动回滚权限，手动回滚请求返回 HTTP 409。

Day 2 为脚本执行轨迹，Day 3 和 Day 5 为真实模型归档，Day 4 脚本验收单独标记为 Mock。没有旧数据时，完成一次 B 也会得到可追溯的持久化实验。

## 架构与数据

```text
React / TypeScript / Vite
          │ 同源 REST 和可续读 SSE
          ▼
FastAPI ──┬── console.sqlite     作业、仓库登记、公开事件游标
          ├── 只读归档适配器      SQLite、评分回执、JSONL、封存文件
          └── 独立 Python 作业进程
               ├── MewCodeRuntime → 原生 Agent.run → 权限检查、独立工作树
               ├── Evaluator → 固定 Docker 镜像 → 评分回执
               └── baseline → optimize → FailureAnalyzer / DefaultStrategyOptimizer
                                           └── VersionManager → 快照、矩阵、版本决策
```

| Web 文件 | 职责和依赖 |
| --- | --- |
| `api.py`、`store.py` | 请求模型、作业生命周期、本机访问控制、SSE 和可重放事件游标 |
| `worker.py`、`custom.py` | 冻结题和注册项目的执行适配，消费原有 Runtime 或 Agent 事件，独立评测及复算 |
| `catalog.py` | 对照引擎的评分矩阵、证据和内容寻址快照生成界面数据，校验已有回执绑定的产物和轨迹摘要 |
| `repositories.py`、`mock.py` | 公开 Git 提交导入、无密钥演示 Provider |
| `frontend/src/` | 五个页面、请求订阅、公共样式和 TypeScript 类型 |
| `tests/` | HTTP、浏览器、SSE 重连、父进程丢失和异常恢复验收 |
| `../mewcode/rsi/orchestrator.py` | 原编排器增加可选阶段通知和起始版本校验。原模块 CLI 保留默认行为 |

引擎继续持有评分规则、冻结题集、安全配置和版本引用。Optimizer 仍只能修改 Prompt、inline Skills 和 Workflow。Web 作业数据库记录进度，版本决策从引擎控制数据库读取。新作业最多同时运行三个。服务用文件锁保证每个 Web 状态目录只有一个管理进程，运行时使用一个 Uvicorn worker。

取消会传播到原异步 Runtime。独立评测已经启动时，作业可能停留在 `cancelling`，直到有超时边界的评分进程完成清理。后端异常退出时，作业通过父进程管道终止；重启会将遗留作业标为 `interrupted`。历史数据可以再次打开。仅回环地址、允许的 Host 和来源能使用接口，写请求还需 `X-RSI-Client: console`。前端只能引用服务端登记的资源 ID。

### 指标口径

- **成功率**在 Overview 中表示已评分运行中获得满分的比例。Evaluations 分别展示运行完成率、满分率、逐项测试通过率和版本均分。
- **Test pass rate** 用可与回执摘要对应的裁判审计记录汇总通过项数和总项数，只公开聚合值。记录不完整时显示 `—`。逐题分数仍取签发的评分回执。
- **Regression rate** 表示同一谱系内，候选均分下降的题目占可比题目的比例。每个版本自己的样本次数随报告保留。成本和 Token 缺少可信记录时展示 `—`。
- 冻结清单没有声明 Train / Validation / Test 划分。“unspecified” 表示未指定；这些对照不能用作独立测试集的泛化结论。
- “Model request” 是已记录的模型调用状态。公开轨迹提供工具、顺序、权限、执行耗时和使用量等字段。Web 作业时间戳记录后端观察事件的时间。旧 JSONL 无逐事件时钟，历史任务列表使用身份文件时间，版本决策使用引擎报告时间。

## HTTP 接口

以下均以 `/api` 开头，完整 schema 以服务的 OpenAPI 为准。

| 资源 | 读取 | 写入 |
| --- | --- | --- |
| 概览 | `dashboard/overview`、`dashboard/metrics`、`dashboard/recent-runs` | |
| Provider、仓库 | `providers`、`repositories`、`repositories/{id}`、`repositories/{id}/files` | `POST repositories` |
| 编码任务 | `tasks`、`tasks/{id}` 及 `trajectory`、`events`、`diff`、`evaluation` | `POST tasks/run`、`POST tasks/{id}/cancel` |
| 进化 | `evolution`、`evolution/{id}`、`evolution/{id}/rounds`、`evolution/{id}/events` | `POST evolution/start`、`POST evolution/{id}/cancel` |
| 评测 | `benchmarks`、`benchmarks/{id}`、`evaluations`、`evaluations/{id}`、`evaluations/compare?left=…&right=…` | `POST evaluations/run` 传入 `task_id`，对封存产物复算；支持事件流和取消 |
| 版本 | `versions`、`versions/{id}`、`versions/{id}/diff` | `POST versions/{id}/rollback` 返回当前治理权限 |

提交作业返回 HTTP 202 和作业 ID。SSE 每条都有 `id` 和 JSON envelope，支持 `Last-Event-ID` 和 `?after=`。已归档的任务可按 JSONL 序号重放，缺失时间戳返回 null。旧进化归档只重放确实持久化的评测和决策。

```sh
curl -sS -H 'Content-Type: application/json' -H 'X-RSI-Client: console' \
  -d '{"repo":"page-settings","provider":"demo-mock","turns":12,"timeout":90}' \
  http://127.0.0.1:8765/api/tasks/run
```

## 验收

后端和前端均启动后，以下命令覆盖核心流程。Playwright 默认使用本机 Google Chrome，其他环境可设置 `RSI_CHROME` 为可执行文件路径，或运行 `.venv/bin/playwright install chromium`。浏览器测试生成的截图保存在系统临时目录。

```sh
webdemo/demo-repository.sh
.venv/bin/python webdemo/tests/api_smoke.py
.venv/bin/python webdemo/tests/browser.py --phase dashboard,coding,evolve,history,cancellation,registered
.venv/bin/python webdemo/tests/recovery.py
.venv/bin/python -B -m pytest -q tests
```

真实模型浏览器验收为显式选项。将名称设为本机已配置的 Provider 后执行。

```sh
RSI_REAL_PROVIDER=deepseek-official .venv/bin/python webdemo/tests/browser.py --phase real
```

`recovery.py` 创建独立临时数据目录和服务端口，验证实时断线续读、产物复评分、父进程丢失、重启后查询，以及 Docker 不可用时无评分回执。其他测试会在正在使用的 Web 服务中创建带来源标识的实验。如需隔离验收，可给两项服务使用单独的 Web 数据目录、代理地址和端口。

## 常见问题

- **启动后没有历史实验**。检查 `RSI_ARCHIVE` 是否指向包含 Day 2–5 子目录的归档父目录。新安装可直接使用 Mock 创建实验。
- **评分失败或找不到镜像**。确认 Docker 正在运行、支持冻结架构，再执行安装步骤中的精确摘要拉取。可以用 `docker image inspect` 核对该摘要。本机验收与原项目一致使用 `DOCKER_API_VERSION=1.54`，只有相应 Docker 版本才需要此设置。
- **进化直接结束**。检查 Stop reason。基线达到目标、轮数用尽、没有提案或提案未通过校验，均是引擎已有的结束原因。
- **看不到公开仓库**。先在服务端配置的公开根目录提交代码，确保顶层存在 `public_checks.py`，然后用直接子目录名注册。导入绑定到注册时的 HEAD；新提交可再次注册。
- **端口或锁占用**。复用已启动的本地服务，或为另一个实例配置独立 Web 状态目录、API 端口、Mock 端口和 Vite 代理。服务运行时不启用多 worker 或自动重载。

