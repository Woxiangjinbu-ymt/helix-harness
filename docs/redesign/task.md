# Coding Agent Workspace 与 RSI Center 开发任务

**状态：已批准。** 2026-11-24 用户批准五个阶段的 116 项任务。依据为已批准的 [产品规格](spec.md) 和 [技术设计](plan.md)。本表用于排定开发和测试工序。各行标示当前实施状态，已执行的验收记录见本文件和 checklist.md。

## 执行顺序与状态

| 阶段 | 任务 | 进入条件 | 阶段完成的依据 |
| --- | --- | --- | --- |
| 1 · Coding 核心体验 | T101–T138 | 四份整改文档批准 | 真实工作树可编程，同树写入竞争和工具授权已通过实测。 |
| 2 · Coding 稳定性与日常使用 | T201–T227 | T138 完成 | 普通 Coding 入口用真实模型完成并恢复多轮编程，同一项长任务实测至少 30 分钟。 |
| 3 · 独立 RSI Center | T301–T323 | T227 完成 | 独立实验的评分、接受、拒绝、主动回滚与旧归档可查。 |
| 4 · 显式协同 | T401–T414 | T323 完成 | 候选与预览一致，指定 Coding 配置可在 RSI 停用后加载所选 Harness。 |
| 5 · 完整回归 | T501–T514 | T414 完成 | 同一演示走完真实编程、RSI 进化和返回原会话，AC01–AC27 均有实测依据。 |

T101 先建立升级前对照，第一阶段其他任务均以此为前置。此后每项依赖本阶段的进入条件，表内另列直接依赖。标为「阶段入口」的工作可以在该阶段开始后独立实施。阶段收尾依赖本阶段全部前项。T225 与 T226、T511 与 T512 分别是同一次运行的前后两步。按任务更新 `TODO → IN_PROGRESS → DONE`。被阻塞时填 `BLOCKED` 并记具体原因及恢复条件，验证通过后记录可回查的测试结果与运行标识。

每项以单一改动或单项核验为单位。执行时按约 2–5 分钟的编辑或测试操作推进。模型调用、实际满 30 分钟的长任务、评分容器和整套回归均按实际耗时取证。五个阶段的收尾记录包括交付功能、实际改动文件、架构变化、自动化测试及真实模型结果、遗留问题和下一阶段工作。

## 文件清单

路径相对 MewCode 项目根目录。任务表中 Web 后端短路径相对 `webdemo/`，`src/` 路径相对 `webdemo/frontend/`，`webdemo/tests/`、`mewcode/`、`tests/rsi/` 和 `docs/` 均从项目根目录起算。`checklist.md` 已批准，用于逐项登记验收结果。

| 文件操作 | 职责 | 文件 |
| --- | --- | --- |
| 调整 | 本地入口与 RSI 作业存储 | `webdemo/api.py`、`webdemo/store.py`、`webdemo/worker.py`、`webdemo/catalog.py`、`webdemo/mock.py` |
| 新增 | RSI 路由与共享 Provider 装配 | `webdemo/rsi_api.py`、`webdemo/providers.py` |
| 新增 | Coding 会话、轮次、事件及事务 | `webdemo/coding/models.py`、`webdemo/coding/store.py`、`webdemo/coding/manager.py`、`webdemo/coding/routes.py` |
| 新增 | 工作树登记、进程监督、Runtime 适配与执行许可 | `webdemo/coding/worktrees.py`、`webdemo/coding/worker.py`、`webdemo/coding/runtime_adapter.py`、`webdemo/coding/actions.py` |
| 新增 | 配置快照、起始材料、RSI 候选与两个跨模块入口 | `webdemo/coding/profiles.py`、`webdemo/coding/exports.py`、`webdemo/candidates.py`、`webdemo/exchange.py` |
| 新增 | 普通会话工作台、设置、事件视图及前端客户端 | `webdemo/frontend/src/pages/CodingWorkspace.tsx`、`webdemo/frontend/src/pages/CodingSettings.tsx`、`webdemo/frontend/src/components/CodingTimeline.tsx`、`webdemo/frontend/src/lib/coding.ts` |
| 调整 | 主导航、RSI 专用上下文、现有实验 API 客户端 | `webdemo/frontend/src/main.tsx`、`webdemo/frontend/src/lib/workspaceContext.tsx`、`webdemo/frontend/src/lib/api.ts` |
| 调整 | 工作台面板样式和双语文案 | `webdemo/frontend/src/styles/pages.css`、`webdemo/frontend/src/i18n/types.ts`、`webdemo/frontend/src/i18n/zh.ts`、`webdemo/frontend/src/i18n/en.ts` |
| 新增 | RSI 题集页 | `webdemo/frontend/src/pages/Benchmarks.tsx` |
| 调整 | 实验概览、单题、评测、进化与版本页 | `webdemo/frontend/src/pages/Dashboard.tsx`、`webdemo/frontend/src/pages/Workspace.tsx`、`webdemo/frontend/src/pages/Evaluations.tsx`、`webdemo/frontend/src/pages/Evolution.tsx`、`webdemo/frontend/src/pages/Versions.tsx` |
| 调整 | 谱系控制库、版本决策和固定计划的单题调度 | `mewcode/rsi/versions.py`、`mewcode/rsi/orchestrator.py` |
| 新增测试 | Coding 生命周期与跨模块契约 | `webdemo/tests/test_coding_lifecycle.py`、`webdemo/tests/test_exchange.py` |
| 扩展测试 | HTTP 冒烟、浏览器、恢复和版本事务 | `webdemo/tests/api_smoke.py`、`webdemo/tests/browser.py`、`webdemo/tests/recovery.py`、`tests/rsi/test_day4.py` |
| 复用 | 原生执行、对话恢复、模型及权限 | `mewcode/agent.py`、`mewcode/memory/session.py`、`mewcode/config.py`、`mewcode/client.py`、`mewcode/permissions/checker.py`、`mewcode/tools/bash.py` |
| 复用 | 原生 Hooks、Skills、MCP 和两种原生入口 | `mewcode/hooks/engine.py`、`mewcode/skills/loader.py`、`mewcode/mcp/manager.py`、`mewcode/__main__.py`、`mewcode/app.py` |
| 复用 | 独立评分、证据核验、实验 Runtime、策略与冻结校验 | `mewcode/rsi/evaluator.py`、`mewcode/rsi/matrix.py`、`mewcode/rsi/runtime.py`、`mewcode/rsi/strategy.py`、`mewcode/rsi/fixtures.py` |
| 复用 | 文件、Diff 视图及已有构建入口 | `webdemo/frontend/src/components/CodeViewer.tsx`、`webdemo/frontend/src/components/DiffViewer.tsx`、`webdemo/frontend/package.json` |
| 更新阶段记录 | 实施后逐项更新任务与验收结果 | `docs/redesign/task.md`、`docs/redesign/checklist.md` |

## 测试入口

- Coding 存储、进程、权限与恢复的小改动用临时 Git 仓库和独立数据库复核，通过 `.venv/bin/python -B -m pytest -q webdemo/tests/test_coding_lifecycle.py` 运行。跨模块用例对应 `webdemo/tests/test_exchange.py`，版本事务用例对应 `tests/rsi/test_day4.py`，可以按 pytest 节点 ID 单独执行。HTTP 路由装配前直接测试其处理函数，接通后再验证相同输入的 HTTP 结果。每项的测试列给出输入或操作和可判定的结果。
- 前端使用 `npm --prefix webdemo/frontend run build`。从项目根按 [Web 服务与测试说明](../../webdemo/README.md) 启动独立验收实例，执行 `.venv/bin/python webdemo/tests/api_smoke.py` 和 `.venv/bin/python webdemo/tests/recovery.py`。原生及 RSI 全量回归用 `.venv/bin/python -B -m pytest -q tests`。
- 浏览器用 `.venv/bin/python webdemo/tests/browser.py --phase <场景名>`。保留已有 `dashboard,coding,evolve,history,cancellation,registered` 和 RSI 的 `real` 场景。T224、T225–T226 与 T511–T512 分别交付 `coding_real`、`coding_long` 和 `coding_rsi_roundtrip`。真实模型验收使用服务端实际配置的 Provider，记录模型身份和运行 ID。受控的竞态、错误和版本决策样例记录其演示来源。
- 每次验证使用该行指定的业务入口。数据库断写、服务中断、链接指向范围外和回滚并发等故障注入均使用验收副本。用 [项目测试约定](../../AGENT.md) 中的 tmux 流程核对真实 TUI 会话。

## Phase 1 · Coding 核心体验

### 既有入口与工作树

| 任务与目标 | 直接依赖 | 涉及文件与实施内容 | 测试操作与通过条件 | 状态 |
| --- | --- | --- | --- | --- |
| T101 · 留下升级前的对照 | 文档批准 | `webdemo/tests/recovery.py`。在改动入口前，用旧版本在隔离目录保存可恢复的原生会话样本、冻结发布标识、旧作业 ID 和只读归档副本的清单。记录轨迹、回执、快照、产物摘要及分数、版本、来源。 | 用原标识打开各样本，独立读取副本得到相同摘要，保留基线供 T513 比对。（AC26） | DONE |
| T102 · 分离旧作业路由 | T101 | `api.py`、`rsi_api.py`。将现有 RSI 路由登记移到独立路由组，保留旧路径和本机写请求校验。 | 用旧作业 ID 查详情和事件仍成功，来自非受信来源的写请求返回 403。（AC05、AC26） | DONE |
| T103 · 隔离 RSI 启动资源 | T102 | `api.py`、`rsi_api.py`、`mock.py`。将旧作业库、归档探测和 Mock 服务的启动移到对应业务首次需要的入口。区分应用存活与实验设施状态。 | 禁用评分设施与 Mock 后启动 webdemo.api:app，基础服务仍响应，访问受影响的实验操作才显示原因。（AC19、AC25） | DONE |
| T104 · 登记实际工作树 | 阶段入口 | `coding/worktrees.py`、`coding/store.py`。从起始目录解析 Git 根与专属 Git 管理目录，以规范路径及设备、文件序号查登记，保留相对起始目录。 | 登记根、子目录和路径别名得到同一工作树 ID，登记同仓库的另一 linked worktree 得到另一 ID。（AC01、AC06） | DONE |
| T105 · 限制路径解析 | T104 | `coding/worktrees.py`。为现存文件和新目标父目录实现授权范围检查，打开目录或写入前复核真实目标。 | 在临时仓库试普通路径及指向范围外的链接，合法读写成功，越界目标与目录清单保持原值。（AC23） | DONE |
| T106 · 列出和搜索当前文件 | T105 | `coding/worktrees.py`、`coding/routes.py`。对已登记工作目录提供列表、读取、按名称及内容搜索，包括仍未提交的文件。 | 按名称和文件内的独有文字搜索同一未跟踪文件，结果路径一致，读取与磁盘字节相同。（AC03、AC23） | DONE |
| T107 · 读取工作树差异 | T105 | `coding/worktrees.py`、`coding/routes.py`。返回新增、修改、删除和暂存状态与逐文件差异，使用工作树中的新文件内容。 | 预置暂存及未暂存修改、未跟踪文件和删除项，逐项核对 Git 状态、索引与工作树差异。（AC03） | DONE |

### 普通会话、轮次与事件

| 任务与目标 | 直接依赖 | 涉及文件与实施内容 | 测试操作与通过条件 | 状态 |
| --- | --- | --- | --- | --- |
| T108 · 建会话与轮次表 | T104 | `coding/models.py`、`coding/store.py`。定义 CodingSession、CodingTurn 及绑定关系，在仓库外的 Coding 数据目录建表，限制一个会话仅一条未结束轮次。 | 用两个连接同时插入同会话活动轮次，仅一条成功。关闭再打开库，原目录绑定仍可查询。（AC01、AC22） | DONE |
| T109 · 保存事件序号 | T108 | `coding/models.py`、`coding/store.py`。为 CodingEvent 实现会话内 seq 分配、source_id 去重、轮次归属检查和提交后通知。 | 重送同一来源事件得到原序号。两个会话各自有序，事务回滚时订阅者收不到事件。（AC04、AC08） | DONE |
| T110 · 保存操作回执 | T108 | `coding/store.py`。按对象及请求 ID 保存规范化载荷与结果，由调用方与业务变更共用事务，先查回执再查可变状态。 | 同 ID 同载荷在对象结束后仍读到原结果。异载荷冲突，事务中断后没有孤立回执。（AC22） | DONE |
| T111 · 固定轮次模型配置 | T108 | `providers.py`、`coding/profiles.py`、`coding/models.py`。复用原生配置解析与客户端工厂，为首个可用 Coding 配置生成 `CodingConfigSnapshot`，持久化无秘密的规范内容及摘要。 | 相同内容两次得到同一 snapshot_id，切换模型得到新摘要。旧内容可查，密钥标记在快照内匹配零次。（AC02、AC11、AC24） | DONE |
| T112 · 创建与重开会话 | T103、T104、T108、T111 | `coding/routes.py`、`coding/manager.py`、`coding/store.py`。按仓库创建、列出和打开会话，在新轮执行前复核目录身份，找不到原目录时保留可读历史。 | 为 A、B 各建会话，移动 A 并在原位置换另一仓库，重开 A 仍可读历史，无法在替代目录提交执行。（AC01） | DONE |
| T113 · 原子受理轮次 | T109、T110、T111、T112 | `coding/manager.py`、`coding/store.py`。用一次事务保存 submit_request_id、用户消息、固定快照、轮次及受理事件，提交后才可调度。 | 同一次提交重复得到原 turn_id 且用户消息只有一条。不同文本复用 ID 及另一活动提交分别返回冲突。（AC02、AC22） | DONE |
| T114 · 建立会话进程通道 | T113 | `coding/worker.py`、`coding/manager.py`。按会话启动专属工作进程，绑定 cwd、worker_instance_id 与私有双向通道，检查消息归属。 | 并发启动两个临时会话，进程身份和工作目录分别正确。发自过期通道的事件无法追加。（AC19） | DONE |
| T115 · 确认执行接手 | T114 | `coding/worker.py`、`coding/runtime_adapter.py`、`coding/manager.py`。实现 start_turn 和接手应答。适配器收到持久化接手确认再进入 Agent.run，重送接手通知沿用 source_id。 | 丢弃一次接手应答，观察同实例重送，模型入口计数为一。接手前退出时无模型调用。（AC10、AC22） | DONE |
| T116 · 接回原生对话 | T115 | `coding/runtime_adapter.py`、`mewcode/memory/session.py`。按 history_cursor 解码原生消息，使用仓库外的会话记录及长输出目录，装配 Agent.run 所需对话。 | 用记录输入核对第二轮上下文中首轮消息及新用户消息各一次，超长工具输出可从本会话目录回读。（AC02、AC09） | DONE |
| T117 · 保存实际模型事件 | T109、T116 | `coding/runtime_adapter.py`、`coding/manager.py`。将文本片段和定稿关联到同一 message_id，仅转发 Runtime 实际发布的可展示思考、工具、用量和错误。 | 注入有、无可展示思考的原生事件序列，检查消息只定稿一次、思考记录数与来源相同。（AC02、AC04） | DONE |

### 工具授权与工作树写入

| 任务与目标 | 直接依赖 | 涉及文件与实施内容 | 测试操作与通过条件 | 状态 |
| --- | --- | --- | --- | --- |
| T118 · 绑定并行工具调用 | T115 | `coding/runtime_adapter.py`、`coding/actions.py`。在原生直接执行及需询问的分派路径传递实际工具 ID、迭代位置与 call_id，遇到歧义停止该调用。 | 并行运行两个同名调用，一成一败，结果分别带原 call_id，错误只落在失败项。（AC04、AC05） | DONE |
| T119 · 登记待授权操作 | T109、T118 | `coding/manager.py`、`coding/runtime_adapter.py`、`coding/store.py`。实现 request_permission，将原生 Future 与实例、调用、目标、范围绑定，事务内登记等待状态。 | 刷新视图可用同一 permission_id 查询原目标。目标没有磁盘变化，重送登记只产生一项等待。（AC05） | DONE |
| T120 · 交付权限答复 | T110、T119 | `coding/routes.py`、`coding/manager.py`、`coding/runtime_adapter.py`。实现 reply 与 deliver_reply，依次记录决定、交给原 Future、确认交付。记录允许范围与拒绝反馈。 | 跨会话、已失效或相反答复被拒。有效答复重送只唤醒一次，拒绝理由回到原调用。（AC05、AC22） | DONE |
| T121 · 原子取得写入权 | T104、T108、T110 | `coding/models.py`、`coding/store.py`、`coding/manager.py`。实现 WorktreeWriteLease 的 free、held、draining 及递增 generation，将持有轮次和执行实例绑定到实际树。 | 用两个连接按别名竞争同一树仅一个成功，另一方得到持有会话。只读查看和另一树写入可继续。（AC06） | DONE |
| T122 · 在派发前登记动作 | T118、T120、T121 | `coding/actions.py`、`coding/manager.py`、`coding/store.py`。实现 begin_action，将动作 ID、范围、权限、写入权和执行意图一次提交。读动作核验范围，未知效果按可写处理。 | 让事务失败、许可应答丢失、同一许可被消费后重送。前者无派发，其余动作的实际执行次数至多一。（AC06、AC10、AC25） | DONE |
| T123 · 在执行入口复核策略 | T105、T122 | `coding/actions.py`、`coding/runtime_adapter.py`。文件及 Shell 入口复核沙箱、强制拒绝、许可代号与当前目标。需新授权时先结束未派发动作，再以新动作申请。 | 沙箱不可用和规则收紧时命令未启动。补获有效授权后只产生一次预期文件修改。（AC05、AC23） | DONE |
| T124 · 检测外部编辑 | T123 | `coding/actions.py`、`coding/worktrees.py`。读取时留存目标内容版本，编辑与删除前比对实际内容。内容变化时要求重新读取。 | 在读到写之间由编辑器修改同文件，记录显示重新读取先于新写操作，外部改动可核对。（AC03、AC06） | DONE |
| T125 · 约束扩展与授权规则写入 | T122、T123 | `coding/actions.py`、`coding/runtime_adapter.py`。将前后置 Hook、扩展及持续授权规则保存接入相同动作许可。规则写入成功后才记录持续生效。 | 前置 Hook 与主调用分别有许可。另一会话持有写入权时扩展不能写，规则保存失败时界面无持续生效记录。（AC05、AC06） | DONE |
| T126 · 监督可写子进程 | T122 | `coding/worker.py`、`coding/actions.py`。在启动 Shell、Hook 等子进程前登记所属轮次，保存进程组与启动身份，通过监管通道获取退出证据。 | 在临时仓库启动父进程先退出、子进程仍写的测试命令。管理器仍识别存活子进程及所属轮次。（AC06、AC07） | DONE |
| T127 · 凭清理证据释放写入权 | T121、T125、T126 | `coding/actions.py`、`coding/manager.py`、`coding/store.py`。实现 end_action 的结果去重。同代全部可写动作和后代结束才释放，迟到的旧代应答丢弃。 | 先交回工具输出再结束子进程，第二会话只在后者结束后能写。重送旧释放消息不影响新代。（AC06、AC07） | DONE |
| T128 · 隔离模型凭据与工具环境 | T111、T123、T125 | `coding/worker.py`、`coding/runtime_adapter.py`、`providers.py`。仅向对应模型客户端交付凭据，向 Shell、测试、Hook、MCP 子进程提供清理过的环境和允许的显式配置。 | 用识别标记请求测试 Provider，再从项目命令和扩展子进程读取环境，只有目标 Provider 收到标记。（AC24） | DONE |

### Web 入口与首轮编程

| 任务与目标 | 直接依赖 | 涉及文件与实施内容 | 测试操作与通过条件 | 状态 |
| --- | --- | --- | --- | --- |
| T129 · 提交整轮终态 | T117、T127 | `coding/runtime_adapter.py`、`coding/manager.py`、`coding/store.py`。实现 finish_turn，核对最后来源序号、整个 Agent.run 返回值及清理证据，条件提交一次终态事件。 | 让原生 TurnComplete 后继续调用第二个工具，Web 仍运行。整轮退出后刷新两页只看到同一终态。（AC04、AC22） | DONE |
| T130 · 接通提交与实时订阅 | T113、T117、T120、T124、T125、T127、T128、T129 | `api.py`、`coding/routes.py`。装配 Coding 路由。挂接 POST /turns、授权答复、GET /view 和 GET /events，提供首屏读快照及提交后增量，沿用本机访问和归属校验。 | 真实服务接收本地请求返回 202，结束前已有已提交片段。错误来源、错配父子对象、存储失败分别返回 403、404、503。（AC02、AC05、AC25） | DONE |
| T131 · 设定默认工作台路由 | T112、T130 | `src/main.tsx`、`src/lib/workspaceContext.tsx`、`src/pages/CodingWorkspace.tsx`。将默认、新会话与 `#coding/<session_id>` 路由到普通工作台。把公开题选择限定在 RSI，保留 `#workspace/<job_id>`。 | 分别直达空地址、Coding 会话和旧作业链接，网络请求和渲染记录属于各自业务，普通首页无需实验健康探测。（AC01、AC19、AC26） | DONE |
| T132 · 接入会话选择与输入 | T130、T131 | `src/pages/CodingWorkspace.tsx`、`src/lib/coding.ts`。侧栏选择目录、新建与重开会话。发送时复用单次操作 ID，中心输入框按会话保存草稿。 | 在两个仓库之间切换再返回，原目录、会话与未发送草稿正确。双击提交只出现一个轮次。（AC01、AC02、AC22） | DONE |
| T133 · 按调用渲染时间线 | T117、T118、T130、T132 | `src/pages/CodingWorkspace.tsx`、`src/components/CodingTimeline.tsx`、`src/lib/coding.ts`。按消息和调用身份归并事件，显示真实文本、可用思考、参数、工具结果和授权卡片，提供三种现有授权决定。 | 用两次并行同名调用与一项需询问操作打开详情，原参数、权限范围和结果逐项相符。（AC04、AC05） | DONE |
| T134 · 显示终端与测试证据 | T126、T133 | `coding/actions.py`、`src/components/CodingTimeline.tsx`。把每次 Shell 的实际命令、cwd、输出、退出状态连到调用卡片。无可靠结论时显示结果未知。 | 依次运行失败测试、修复后重跑和无可靠测试结论的命令，三个视图与原始输出、退出证据一致。（AC02、AC04） | DONE |
| T135 · 连接文件与差异面板 | T106、T107、T132 | `src/pages/CodingWorkspace.tsx`、`src/lib/coding.ts`、`src/styles/pages.css`。复用 CodeViewer、DiffViewer 连接浏览、搜索和逐文件 Git 差异。按需展开工具、终端和文件面板。 | 打开暂存与未暂存文件、切换详情再收起，内容和 Git 相符，输入草稿仍在。（AC01、AC03） | DONE |
| T136 · 生成本轮摘要 | T124、T133、T134、T135 | `src/components/CodingTimeline.tsx`、`src/pages/CodingWorkspace.tsx`。从调用记录汇总测试、未完成项和能归属的变更。对外部混合编辑标明来源未确定。 | 对同文件混合编辑，核对仓库总览含全部修改，本轮摘要的每个确定项都能打开相应调用。（AC03、AC04） | DONE |
| T137 · 对齐工作台两种语言 | T133、T134、T135、T136 | `src/i18n/types.ts`、`src/i18n/zh.ts`、`src/i18n/en.ts`、`src/pages/CodingWorkspace.tsx`。补齐会话、权限、冲突、未知与失败状态的中英文键，并接入原语言切换。 | 切换两种语言并构建。同一调用状态和权限范围在两个界面均可识别。（AC01、AC04、AC05） | DONE |
| T138 · 验收第一阶段 | 本阶段全部前项 | `webdemo/tests/browser.py`、`webdemo/tests/test_coding_lifecycle.py`、`docs/redesign/task.md`、`docs/redesign/checklist.md`。在真实 Coding 入口跑解释、修改、测试场景。通过路径别名竞争写入，核对权限、草稿和外部编辑，登记阶段证据。 | 逐项保存 AC01、AC03–AC06 和 AC02 编程部分的运行标识、Git 差异及测试输出。对照磁盘只有获权动作成功。（AC01、AC02、AC03、AC04、AC05、AC06） | DONE |

### 第一阶段交付记录 · 2026-11-24

T101–T138 已完成。默认入口可以在本机 Git 工作目录创建普通会话、授权编辑、执行测试、检查文件与差异。原生会话和 RSI 旧归档已经在入口改动前保存，阶段结束时重新恢复和比对通过。[升级前基线](</Users/tianmu/Library/Application Support/MewCode/Redesign/T101/baseline.json>) · [阶段证据清单](</Users/tianmu/Library/Application Support/MewCode/Redesign/T138/manifest.json>)

| 交付 | 实际改动与验证 |
| --- | --- |
| 会话与路由 | `webdemo/api.py`、`rsi_api.py`、`providers.py` 与 `coding/{models,store,profiles,routes,worktrees}.py` 分开管理普通会话和实验资源。默认页面只请求 Coding 接口，旧作业 `a53cface0df74ce9b31194f931b8b8ad` 的详情和轨迹仍可通过旧链接打开。|
| Runtime 与工具 | `coding/{manager,worker,runtime_adapter,actions}.py` 让每个活动轮次在独立进程运行原生 Agent。只读调用可并行，写操作、同步命令 Hook 和规则保存经过服务端授权及工作树写入权检查。本机 Shell 用 macOS 沙箱限制文件访问，用独立审计会话的结束通知核对子进程清理。延迟第一个读取的用例中，第二个读取先结束，两个结果仍匹配原调用。|
| 浏览器 | `frontend/index.html`、`src/main.tsx`、`pages/CodingWorkspace.tsx`、`components/CodingTimeline.tsx`、`lib/{api,coding}.ts`、`styles/pages.css` 与 `i18n/{types,en,zh}.ts` 接入实际会话、草稿、三种权限答复、文件、Diff、终端及轮次摘要。启动说明见 [Web 工作台](../../webdemo/README.md)。 |
| 真实模型验收 | `deepseek-official / deepseek-flash` 在普通浏览器会话 `741ef584-71e7-4ed6-9161-c3a46f067cdc` 运行轮次 `ceed2ce1-c521-41fb-a716-afe7029c23ad`。同一条 `python3 -m unittest` 先退出 1，编辑 `maths.py` 后退出 0，独立重跑通过。[真实模型轮次](</Users/tianmu/Library/Application Support/MewCode/Redesign/T138/real/view.json>) · [Git 补丁](</Users/tianmu/Library/Application Support/MewCode/Redesign/T138/real/changes.diff>) · [独立测试输出](</Users/tianmu/Library/Application Support/MewCode/Redesign/T138/real/unittest.txt>) |
| 自动验证 | 新增 `webdemo/tests/test_coding_lifecycle.py`，扩展 `browser.py`、`recovery.py`。Coding 专项 13 项通过。三个浏览器场景 `coding_workspace`、`coding_controls`、`coding_legacy` 通过。前端构建通过。原生与 RSI 套件 742 项通过、17 项跳过；各跳过原因在测试日志中。[浏览器结果](</Users/tianmu/Library/Application Support/MewCode/Redesign/T138/browser.txt>) · [专项测试](</Users/tianmu/Library/Application Support/MewCode/Redesign/T138/lifecycle.txt>) · [原生套件](</Users/tianmu/Library/Application Support/MewCode/Redesign/T138/native.txt>) |
| 平台与交接 | Shell 写进程监督在 macOS ARM64 登录会话实测。当前其他平台在命令启动前报告沙箱不可用。第二阶段从 T201 接入取消，随后验收服务重启恢复、Web 配置及扩展、真实多轮续聊和至少 30 分钟的同一任务。 |

## Phase 2 · Coding 稳定性与日常使用

### 取消、断线与恢复

| 任务与目标 | 直接依赖 | 涉及文件与实施内容 | 测试操作与通过条件 | 状态 |
| --- | --- | --- | --- | --- |
| T201 · 受理取消 | 阶段入口 | `coding/manager.py`、`coding/routes.py`、`coding/store.py`。实现 cancel 的条件更新，把 cancelling、未答授权失效、停止新派发和请求回执放入同一事务。 | 重复取消返回同一回执。用两个连接竞争完成和取消，只出现一条允许的状态路径。（AC07、AC22） | DONE |
| T202 · 停止所属轮次 | T201 | `coding/worker.py`、`coding/runtime_adapter.py`。处理 stop_turn，分别撤销未启动调度、丢弃未消费许可、结束授权等待、停止原生循环及本轮子进程。 | 在接手前、等待答复和写命令执行中分别取消，旧 Future 失效，另一会话的命令仍运行。（AC07、AC19） | DONE |
| T203 · 核实延迟清理 | T202 | `coding/worker.py`、`coding/manager.py`。超时后将占用标为 draining，记录阻塞原因。适配器无应答时由管理器终止所属执行，按进程启动身份复查。 | 让临时写进程延迟退出，仍可读、无法接手写入。核对后代消失后才发布 cancelled 并可重新取得写入权。（AC07、AC13） | DONE |
| T204 · 在会话中取消与继续 | T201、T203 | `src/pages/CodingWorkspace.tsx`、`src/lib/coding.ts`、`src/components/CodingTimeline.tsx`。将取消按钮绑定到会话和轮次，显示取消中、阻塞原因与最终状态，保留原 Diff 与后续输入。 | 两标签页分别取消、刷新，终态一致。重开可查看保留的文件修改并在同一会话提交下一轮。（AC07、AC22） | DONE |
| T205 · 生成一致的恢复视图 | 阶段入口 | `coding/store.py`、`coding/routes.py`。在一次读事务中取得消息、工具、待授权、状态与 through_seq，订阅从该序号后读取。 | 在取得视图与订阅之间提交一个事件，客户端补读到一次，视图中已有的最后事件没有重现。（AC08） | DONE |
| T206 · 验证 SSE 游标 | T205 | `coding/routes.py`。按会话解析 Last-Event-ID 与 after，前者优先。非法、超前或过期位置发 reset_required，心跳不占序号。 | 逐项提交上述游标，检查补发范围与重置响应。一轮结束后原连接仍能收到下一轮。（AC08） | DONE |
| T207 · 在客户端恢复订阅 | T206 | `src/lib/coding.ts`、`src/pages/CodingWorkspace.tsx`。保留断线前显示的记录，仅在成功应用事件后更新游标。重置时读取 /view 再续订。 | 在含工具与授权的轮次断线两次，覆盖离线完成及应用失败。最终逐项对齐服务端顺序、数量及磁盘计数。（AC08） | DONE |
| T208 · 恢复失联的轮次 | T203、T205 | `coding/manager.py`、`coding/store.py`、`coding/worker.py`。启动时核对活动实例与进程身份，失联轮次提交 interrupted，使旧授权失效，并继续监管其 draining 记录。 | 结束临时服务并重启，已完成的对照轮次保持原终态，失联轮次显示中断，残余可写进程退出后才可交接。（AC07、AC10） | DONE |
| T209 · 呈现未确认的动作结果 | T208 | `coding/manager.py`、`src/components/CodingTimeline.tsx`。从已保存的意图和回执识别执行结果未知的调用，链接当前 Diff。恢复历史时仅还原证据。 | 在写入一次计数标记后、保存结果前中断，反复恢复仍仅一条磁盘标记，可由用户新开一轮。（AC10） | DONE |
| T210 · 持久化压缩边界 | 阶段入口 | `coding/runtime_adapter.py`、`coding/store.py`。通过 append_event 保存原生消息定稿、压缩摘要、保留范围与后续记录。时间线保留早期事件。 | 触发一次实际原生压缩，重开只读视图能看到早期工具事件、压缩通知及对应边界。（AC09） | DONE |
| T211 · 按最后边界重建模型输入 | T210、T208 | `coding/runtime_adapter.py`、`coding/worker.py`。按原生恢复规则装配摘要、保留的近期消息和之后的新消息，空闲进程释放后从相同记录重建。 | 重启后用含已知标记的对话追问，检查捕获的模型输入及回答，摘要与近期详情各一次。（AC09） | DONE |

### 设置、扩展与故障反馈

| 任务与目标 | 直接依赖 | 涉及文件与实施内容 | 测试操作与通过条件 | 状态 |
| --- | --- | --- | --- | --- |
| T212 · 持久化 Provider 配置 | 阶段入口 | `coding/profiles.py`、`providers.py`、`coding/routes.py`。提供 Web 配置读写，服务端保存或引用版本化凭据，仅返回掩码和非秘密配置。 | 通过 API 保存两份配置，重读及失败响应中凭据标记均为零，所选测试 Provider 收到正确凭据。（AC11、AC24） | DONE |
| T213 · 按轮生效与立即撤权 | T212 | `coding/profiles.py`、`coding/manager.py`、`coding/runtime_adapter.py`。提交时固定端点、模型、预算、权限和扩展版本。派发前检查凭据是否已撤销及当前强制拒绝。 | 在一轮中改模型，本轮实际请求与下一轮分属两个快照。撤销凭据后相应调用报告错误。（AC11、AC24、AC25） | DONE |
| T214 · 接入设置页 | T212、T213 | `src/pages/CodingSettings.tsx`、`src/lib/coding.ts`、`src/main.tsx`、`providers.py`、`mock.py`。选择受支持 Provider、模型和显式的演示来源，显示掩码、配置版本与连接错误。 | 用两份配置发起请求。真实配置错误可定位到原模型，演示来源有标识，旧轮次仍显示原模型。（AC11、AC25） | DONE |
| T215 · 加载会话 Skill | T213 | `coding/profiles.py`、`coding/routes.py`、`coding/runtime_adapter.py`、`src/pages/CodingSettings.tsx`。列出可用 Skill，保存 Web 选择，通过原生加载器将所选版本装入对应会话并回报失败原因。 | 加载带识别行为的 Skill 并调用。另一配置无该行为，损坏的 Skill 显示加载原因。（AC12） | DONE |
| T216 · 管理 MCP 连接 | T213 | `coding/runtime_adapter.py`、`coding/routes.py`、`src/pages/CodingSettings.tsx`。复用原生 MCP 管理器，显示连接、断开及已启用工具。把工具调用关联到本会话的动作许可。 | 连接本地测试服务，调用返回独有标记。断开后工具不可用，连接失败能在设置中定位。（AC12） | DONE |
| T217 · 记录 Hook 的执行结果 | T213 | `coding/runtime_adapter.py`、`src/components/CodingTimeline.tsx`、`src/pages/CodingSettings.tsx`。装载已选 Hooks 的固定配置，展示适用的扩展、Hook 来源及 Runtime 提供的结果。 | 让两会话使用不同 Hook，只有目标会话收到标记事件，写动作可追到本次许可。（AC12、AC19） | DONE |
| T218 · 保存长输出与任务进度 | 阶段入口 | `coding/actions.py`、`coding/store.py`、`src/components/CodingTimeline.tsx`。将有界输出块或长结果文件关联到调用，保留命令、目录、终态及可见的分段或截断说明。 | 同轮静默后连续输出超出页面窗口，仍可查询最早留存段或截断标记、最终退出状态。（AC04、AC13） | DONE |
| T219 · 处理日志写入失败 | 阶段入口 | `coding/manager.py`、`coding/runtime_adapter.py`、`src/lib/coding.ts`。对提交失败停止新的工具与写入派发，通知当前会话存储故障，保留同次操作 ID 与此前证据。 | 使临时库写入失败，服务返回或推送故障状态，随后命令计数不增长。恢复后可按原请求查询。（AC25） | DONE |
| T220 · 区分各类 Coding 错误 | T207、T214、T219 | `src/components/CodingTimeline.tsx`、`src/pages/CodingWorkspace.tsx`、`src/i18n/types.ts`、`src/i18n/zh.ts`、`src/i18n/en.ts`。把断线、拒绝、模型错误、命令失败和存储故障映射到对应轮次与恢复动作。 | 触发每类故障并切换语言，原模型身份、受影响调用和前序证据可查，未知结论有明确标识。（AC25） | DONE |

### 普通会话实测

| 任务与目标 | 直接依赖 | 涉及文件与实施内容 | 测试操作与通过条件 | 状态 |
| --- | --- | --- | --- | --- |
| T221 · 覆盖动作与请求竞争 | T201、T203、T209、T219 | `webdemo/tests/test_coding_lifecycle.py`。用临时库和独立连接重送轮次、权限、取消与动作，并在完成和取消之间并发提交。 | 运行该文件的竞态用例，确认一个受理、一个终态、一个计数标记。旧许可或越权答复均无法派发。（AC05、AC06、AC07、AC22） | DONE |
| T222 · 覆盖服务重启与续读 | T207、T208、T209、T211 | `webdemo/tests/recovery.py`。在恢复脚本添加独立 Coding 数据目录、可控中断点和同次会话的客户端/服务端事件比对。 | 运行恢复脚本，分别核对断线补读、未知结果及压缩后的上下文，旧 RSI 恢复场景仍通过。（AC08、AC09、AC10） | DONE |
| T223 · 验证 Coding 可独立工作 | T212、T214 | `webdemo/tests/api_smoke.py`。在无 Benchmark、归档、Mock 服务及评分设施的环境新建并恢复普通会话，另行检查写请求来源。 | Coding 服务可读取未提交文件并在同一会话继续。无效来源被拒。记录会话 ID 和工具结果。（AC05、AC19） | DONE |
| T224 · 验收真实多轮与恢复 | T204、T207、T211、T214 | `webdemo/tests/browser.py`。在 browser.py 增加普通会话场景 coding_real，使用真实 Provider 解释失败测试、追问修复、关闭重开再询问。 | 从浏览器取得模型标识、失败与成功的同项测试输出、工具记录和 Git Diff，独立重跑通过。（AC02） | DONE |
| T225 · 启动长任务并关闭页面 | T203、T207、T218 | `webdemo/tests/browser.py`。在独立验收仓库增加 coding_long 场景，设超过 30 分钟的预算，启动含静默及长输出的本地任务，切换会话后关闭全部页面。 | 保存服务端开始时间、turn_id 和调用标识。确认浏览器上下文无打开页面，服务端任务仍在推进。（AC13） | DONE |
| T226 · 重开并验收满 30 分钟 | T225 | `webdemo/tests/browser.py`。同一场景重开原会话，查看离线期间的进度。实际运行至少 30 分钟后从 Web 取消并核对清理。 | 同一 turn_id 的服务端开始与取消时间差至少 1800 秒，长输出与终态可查，父子进程回收，另一会话可写。（AC07、AC13） | DONE |
| T227 · 验收第二阶段 | 本阶段全部前项 | `webdemo/tests/test_coding_lifecycle.py`、`webdemo/tests/browser.py`、`docs/redesign/task.md`、`docs/redesign/checklist.md`。核对路径边界、测试凭据、配置与扩展的实测记录，把真实多轮和长任务证据登记到阶段报告。 | AC02、AC07–AC13 及 AC19、AC22–AC25 的 Coding 部分均有操作与结果。真实编程可在原会话继续后开放第三阶段。（AC02、AC07、AC08、AC09、AC10、AC11、AC12、AC13、AC19、AC22、AC23、AC24、AC25） | DONE |

### 第二阶段交付记录 · 2026-11-24

T201–T227 已完成。阶段证据包括实际模型请求、同一轮的服务端时间、命令退出、磁盘计数与完整性摘要。[第二阶段证据清单](</Users/tianmu/Library/Application Support/MewCode/Redesign/T227/manifest.json>)

| 交付 | 实际改动与复核 |
| --- | --- |
| 取消与写入权 | `webdemo/coding/{manager,store,worker,actions}.py` 在一个事务中受理取消、冻结答复并将写入权标为清理中。受管进程通过独立的 macOS 审计会话启动，监督者在服务退出后仍可核实整棵进程树的结束。两个数据库连接竞争终态，延迟清理期间另一会话可读、不能写，另一工作树的写任务继续。 |
| 重启、续读与压缩 | `webdemo/api.py`、`coding/{routes,runtime_adapter}.py`、`src/lib/coding.ts` 和 `CodingTimeline.tsx` 恢复已提交的事件和待答授权。浏览器经历两次断线、一次应用失败和一次游标重置。服务失联后，计数文件仍只有一条标记。真实原生压缩后重启，摘要和保留细节在模型输入中各出现一次。 |
| Provider 与扩展 | `coding/{models,profiles,mcp_bridge}.py`、`CodingSettings.tsx` 和 `CodingWorkspace.tsx` 接通按轮冻结的模型配置、凭据撤销、Skills、MCP 和命令 Hooks。浏览器能看到实际 MCP 工具名、回执及错误来源。失效凭据产生真实端点的 401。 |
| 真实编程 | `deepseek-official / deepseek-flash` 在会话 `728c49c9-3591-4676-a3ad-ddd11b0a5359` 中复现、解释和修复加法错误。关闭全部页面并重开后第三轮能回答原实际值 1 和期望值 5。工作树独立重跑通过。[真实模型、退出码及答复](</Users/tianmu/Library/Application Support/MewCode/Redesign/T227/real/identity.json>) |
| 关页长任务 | 会话 `9e42b882-8a07-4b28-946a-48c993e08a1e` 的轮次 `2334bf1f-6146-4f52-afaa-b92cdb3af58a` 从运行到 Web 受理取消经过 **1801.46 秒**。重开后可见离线产生的输出，保留 2097152 字节并标注截断。取消后两个受测进程均已退出，命令临时目录计数为零，另一会话成功写入。[时间线与清理凭据](</Users/tianmu/Library/Application Support/MewCode/Redesign/T227/long.json>) |
| 自动回归 | 21 项 Coding 生命周期用例通过。前端类型检查和生产构建通过。项目原测试 758 项通过、1 项跳过，并报告一个未注册的 pytest timeout 标记。锁定镜像上的既有 RSI 恢复脚本通过，T101 原生会话及历史归档重新比对一致。[Coding 用例](</Users/tianmu/Library/Application Support/MewCode/Redesign/T227/lifecycle.txt>) · [原测试输出](</Users/tianmu/Library/Application Support/MewCode/Redesign/T227/native.txt>) |
| 验收入口 | `webdemo/tests/test_coding_lifecycle.py`、`api_smoke.py --coding`、`recovery.py --coding`。浏览器场景包括 `coding_real`、`coding_long`、`coding_stability`、`coding_settings`、`coding_extensions` 和 `coding_provider_error`。这些场景分别建立临时 Git 仓库、状态库与服务。 |

本机 Shell 监督在 macOS ARM64 登录会话完成验收。首次长任务验收脚本使用了随最后一页关闭的临时浏览器上下文；改为显式上下文后，两次完整运行分别达到 1801.24 秒和 1801.46 秒。第三阶段从 T301 开始交付独立 RSI Center。

## Phase 3 · 独立 RSI Center

### 题集、评测与单题证据

| 任务与目标 | 直接依赖 | 涉及文件与实施内容 | 测试操作与通过条件 | 状态 |
| --- | --- | --- | --- | --- |
| T301 · 读取 Benchmark 目录 | 阶段入口 | `catalog.py`、`rsi_api.py`。按资源标识返回正式冻结题集、公开示例及待审核候选的分类、题面、来源版本和运行资格。候选列表允许为空。 | 查询既有题集和示例并用待审核的测试样本验证分类，正式评测选择器只列可运行的冻结版本。（AC14） | TODO |
| T302 · 接入 RSI 导航与题集页 | T301 | `src/main.tsx`、`src/pages/Benchmarks.tsx`、`src/pages/Dashboard.tsx`、`src/lib/workspaceContext.tsx`。在 RSI Center 接入 Benchmarks、概览与现有研究页面，把公开项目选择保留在实验视图。 | 从 Coding 进入题集页再返回，原会话仍可用。打开旧作业链接仍显示原实验。（AC01、AC14、AC26） | TODO |
| T303 · 固定实验计划 | T301 | `rsi_api.py`、`worker.py`。从 benchmark_ref、tested_agent_config_id、harness_ref 和预算解析运行条件，每题每次生成固定 `EvalCell`。进化另存优化器配置。 | 读取多题多次的计划，逐项核对重复次数、模型与策略摘要。不适用的快照或冻结条件返回原因。（AC14、AC21） | TODO |
| T304 · 幂等受理实验 | T303 | `rsi_api.py`、`store.py`。对 POST /api/rsi/experiments 保存请求载荷、受理配置和 experiment_id，与作业登记一起提交后再调度。 | 丢弃 202 后重试得到同一实验 ID，作业计数仅增一。同 ID 改预算返回 409。（AC14、AC22） | TODO |
| T305 · 调度单题及实验取消 | T304 | `worker.py`、`mewcode/rsi/orchestrator.py`。将已计划单题交给现有 MewCodeRuntime，在自己的实验目录执行。把取消和清理绑定到该实验。 | 启动多题并取消一项实验，核对其已结束及中断单题的记录，普通工作树无新增文件，另一任务仍推进。（AC14、AC19） | TODO |
| T306 · 保存可核验的评分回执 | T305 | `mewcode/rsi/orchestrator.py`、`worker.py`。由编排器按固定计划构造 `ScoreRequest`，提交给独立 Evaluator，对照单题与封存摘要核验后保存。 | 用原产物评分可查正式回执。错配产物或评分设施故障留下原因，公开示例仅显示公开测试结果。（AC15、AC25） | TODO |
| T307 · 按单题证据恢复 | T306 | `mewcode/rsi/orchestrator.py`。已存回执先核验，已封存且缺评分的单题继续评分，未启动的单题依计划执行，已登记但缺产物的单题保留中断原因。 | 在这四个边界恢复同一计划，核对模型与评分调用次数、各 cell_id 的状态及已有回执。（AC14、AC15） | TODO |
| T308 · 公开轨迹与封存产物 | T306 | `catalog.py`、`rsi_api.py`、`src/pages/Workspace.tsx`。按运行标识提供有序公开事件、对应工具、封存代码和评分回执。读取限制为该次实验的公开材料。 | 从旧单题链接逐项核对产物与回执，用测试标记验证私测文件无法从轨迹或文件接口取得。（AC15、AC23、AC26） | TODO |
| T309 · 对同一产物复算 | T306 | `rsi_api.py`、`worker.py`、`src/pages/Evaluations.tsx`。接通指定 cell_id 的 rescore 接口，保留原裁判与原回执，记录作业 ID、字段比较与不一致原因。 | 对已封存单题复算，产物依据及分数匹配，模型调用计数与活动版本相同。独立评分失败可定位。（AC15、AC25） | TODO |
| T310 · 接入评测运行页 | T303、T304、T305、T307、T309 | `src/pages/Evaluations.tsx`、`src/lib/api.ts`。提交题集、被测配置、快照、预算与重复次数，按题展示实际进度和来源，提供轨迹及复算入口。 | 从浏览器发起多题多次评测，制造一次可辨中断，能定位具体运行并查看其他运行的结果。（AC14、AC15） | TODO |

### 版本晋升、主动回滚与历史

| 任务与目标 | 直接依赖 | 涉及文件与实施内容 | 测试操作与通过条件 | 状态 |
| --- | --- | --- | --- | --- |
| T311 · 区分活动引用与正式榜 | T303 | `mewcode/rsi/versions.py`、`catalog.py`。新谱系核验完整正式基线后才建立初始活动引用。在可写控制库为活动引用加单调 revision，由旧活动头初始化。旧归档通过只读查询解析历史榜和引用。 | 升级旧可写库副本两次只初始化一次，归档副本内容摘要和文件清单相同。活动引用可低于榜上最大版本。（AC17、AC26） | TODO |
| T312 · 登记候选的父引用 | T303、T311 | `mewcode/rsi/versions.py`、`mewcode/rsi/orchestrator.py`。沿用 stage 固定候选、父策略摘要和预期修订，校验提案证据与 Prompt、inline Skills、Workflow 变更范围。 | 合法提案封存可复读，超范围改动或不匹配父摘要被拒，Runtime 源树摘要相同。（AC16） | TODO |
| T313 · 依据完整矩阵决策 | T306、T312 | `mewcode/rsi/versions.py`。沿用矩阵核验和 finish，按每题均分再等权汇总，区分严格提高、平分或下降、证据不完整三种结果。 | 运行扩展的 day4 受控矩阵用例，分别得到接受、拒绝、未完成，页面所需数据能追到逐题回执。（AC16） | TODO |
| T314 · 原子提交晋升 | T311、T313 | `mewcode/rsi/versions.py`。用预期 HeadRef 条件提交正式榜项、接受决定、新头与修订。拒绝保留报告，重试已决候选取原报告。 | 同一父引用并发晋升最多一次成功。已有决定重读同一报告，失败候选可查，正式榜与头一致提交。（AC16、AC17、AC22） | TODO |
| T315 · 解耦在途评测与活动引用 | T307、T314 | `mewcode/rsi/orchestrator.py`、`mewcode/rsi/versions.py`。逐题执行与存回执时校验该题固定计划，在尝试发布候选时才检查活动修订。 | 评测中移动活动引用，原单题可交回原证据，过期候选发布返回 reference_changed。（AC17、AC21） | TODO |
| T316 · 校验回滚目标 | T311、T314 | `mewcode/rsi/versions.py`。按谱系可写属性、目标摘要及正式接受记录检查目标。允许完整正式基线，区分已是当前目标的情况。 | 分别用其他谱系、只读归档、未接受候选与合法旧基线试操作，无效项被拒且状态无变化。（AC17） | TODO |
| T317 · 原子记录主动回滚 | T316 | `mewcode/rsi/versions.py`。实现 rollback，按 request_id 先查回执，把旧引用检查、活动头、修订、决定与回执放进同一写事务。 | 回滚响应丢失后重送得到同一 decision_id。已是目标的请求不新增变更，同 ID 换目标返回冲突。（AC17、AC22） | TODO |
| T318 · 验证回滚与晋升竞态 | T315、T317 | `tests/rsi/test_day4.py`。扩展现有并发晋升与事务中断用例，覆盖同一旧引用的晋升与回滚以及提交前后退出。 | 运行 day4 用例，仅一个头变更，过期方取得最新修订。重启读到变更前或变更后的完整记录。（AC17、AC22） | TODO |
| T319 · 接入回滚 API | T317 | `rsi_api.py`。为谱系回滚路由传入 expected_head 与 target_ref，校验本机写权限。旧接口收到完整字段时委托同一操作。 | 正常返回源、目标及新修订。过期请求回 409 与当前头，旧请求缺预期引用时仍返回冲突。（AC17） | TODO |
| T320 · 展示主动回滚与拒绝 | T314、T319 | `src/pages/Versions.tsx`、`catalog.py`、`src/lib/api.ts`。确认页先展示源版本、目标和策略差异。分别显示主动回滚历史和候选拒绝时保留父版本的记录。 | 用成功和冲突的两次请求刷新版本页，决定数及活动引用与库一致，旧拒绝事件能正确解释。（AC16、AC17） | TODO |
| T321 · 接入失败分析与策略比较 | T308、T313、T314 | `catalog.py`、`src/pages/Evolution.tsx`。将进化页的启动操作接到实验接口并沿用既有优化器，将失败结论、样本轨迹、优化假设、父子快照差异、逐题成绩与决策报告关联。 | 从一次失败结论定位原运行，再打开父子 Prompt、Skill 或 Workflow 差异并用回执复算成绩。（AC16） | TODO |
| T322 · 按记录计算历史指标 | T306、T311 | `catalog.py`、`src/pages/Dashboard.tsx`、`src/pages/Evaluations.tsx`。按轮、来源和样本数聚合成功率、Token、耗时、可得费用。无来源的指标标记未记录。 | 各选一组完整与缺项的真实或演示归档，从原回执独立汇总，页面来源、分母、数值及缺失项符合记录。（AC18、AC25） | TODO |
| T323 · 验收第三阶段 | 本阶段全部前项 | `tests/rsi/test_day4.py`、`webdemo/tests/browser.py`、`src/i18n/types.ts`、`src/i18n/zh.ts`、`src/i18n/en.ts`、`docs/redesign/task.md`、`docs/redesign/checklist.md`。补齐 RSI 操作与失败文案，运行独立谱系的接受、拒绝与主动回滚，复查旧归档并登记阶段记录。 | 核对 AC14–AC18 和 AC26 的归档部分。新实验用回滚后的引用，在途实验保存原快照，既有归档按原 ID 可查。（AC14、AC15、AC16、AC17、AC18、AC26） | TODO |

## Phase 4 · 两个显式的跨模块入口

### 从 Coding 提交 Benchmark 候选

| 任务与目标 | 直接依赖 | 涉及文件与实施内容 | 测试操作与通过条件 | 状态 |
| --- | --- | --- | --- | --- |
| T401 · 保留轮次起始代码 | 阶段入口 | `coding/exports.py`、`coding/manager.py`。在首次模型与工具执行前记录可导出文件的路径、摘要及可恢复内容，固定 Git 对象，单独留存未提交、未跟踪文件及删除状态。 | 创建含三类差异的轮次，随后改工作树并清理无引用 Git 对象。仍能按清单恢复起始代码与删除项。（AC20） | TODO |
| T402 · 处理无法验证的起始材料 | T401 | `coding/exports.py`、`coding/worktrees.py`。按实际路径限制捕获范围，对读取期间变化、越权及缺失来源的文件给出不可用原因。 | 含外链、变化文件及无起始证据的旧轮次仍可普通编程，受限或无法还原的材料不能加入预览。（AC20、AC23） | TODO |
| T403 · 生成不可变预览 | T402 | `coding/exports.py`、`coding/routes.py`。实现 benchmark-previews，以该轮起始文件、题面及标明版本的公开验证材料构建清单，返回 preview_id、manifest_digest 和可审阅内容。 | 改选材料产生相应的新预览，之前的清单与字节不变。错配会话、私测与凭据均不可预览。（AC20、AC23、AC24） | TODO |
| T404 · 封存 RSI 独立副本 | T403 | `candidates.py`、`exchange.py`。在两个业务模块装配时注入只读接口。按确认过的预览从 Coding 只读接口取材料，复核授权、清单和内容摘要后写入 RSI 内容存储。 | 预览后再改原仓库，封存副本与预览逐文件匹配。摘要不符时无候选对外可见。（AC20） | TODO |
| T405 · 幂等登记待审核候选 | T404 | `candidates.py`、`rsi_api.py`。实现 POST /api/rsi/benchmark-candidates，在 RSI 库一次事务登记 pending_review 和请求回执。同请求重试先查回执再查预览。 | 在封存后、提交前及应答丢失处分别重试，候选数只增一，已过期预览的成功请求返回原 ID，异载荷回 409。（AC20、AC22） | TODO |
| T406 · 连接预览、提交与目录 | T403、T405 | `src/pages/CodingWorkspace.tsx`、`src/lib/coding.ts`、`src/pages/Benchmarks.tsx`、`catalog.py`。普通轮次提供材料选择与预览，明确提交后显示候选 ID。题集页展示待审核项及来源。 | 仅结束轮次时实验与候选数不变。提交后可返回会话继续编辑，候选只在待审核分类，冻结题集摘要不变。（AC14、AC20） | TODO |

### 在实验或 Coding 中选用已验证 Harness

| 任务与目标 | 直接依赖 | 涉及文件与实施内容 | 测试操作与通过条件 | 状态 |
| --- | --- | --- | --- | --- |
| T407 · 解析已验证的 Harness | 阶段入口 | `exchange.py`、`mewcode/rsi/versions.py`。提供 resolve_verified_harness 只读接口，核验谱系、版本、接受或正式基线证据、内容摘要，取回策略包。 | 已退出活动位置的接受版本仍可选。未验证、证据缺失或被改过的快照返回明确原因，历史记录仍可读。（AC21） | TODO |
| T408 · 将显式选择固定到实验 | T407 | `rsi_api.py`、`worker.py`、`src/pages/Evaluations.tsx`。在研究者选择的实验中同时固定 tested_agent_config_id 与 harness_ref，记录实际 Provider、模型和快照。 | 按历史已验证版本启动，随后推进或回滚谱系。已计划单题的实际运行仍指向所选版本。（AC14、AC21） | TODO |
| T409 · 将策略包保存到 Coding | T407 | `coding/profiles.py`、`exchange.py`。按 manifest 核对并落盘到仓库外的 Coding 内容存储。装载范围为 Prompt、inline Skills 和 Workflow。 | 读取本地副本可复核摘要。包缺损时不会产生可用绑定，原 Coding 权限、凭据及工具配置仍由原配置提供。（AC21、AC24） | TODO |
| T410 · 按配置修订提交绑定 | T409 | `coding/routes.py`、`coding/profiles.py`、`coding/store.py`。实现 POST /api/coding/profiles/{profile_id}/harness，校验配置归属，再按请求 ID 查回执。事务内更新预期修订、快照引用及回执。 | 同一预期修订竞争仅一个成功。丢失应答后重试能读回原修订，坏摘要或旧修订保留原绑定。（AC21、AC22） | TODO |
| T411 · 从本地绑定启动轮次 | T410 | `coding/profiles.py`、`coding/runtime_adapter.py`。生成 CodingConfigSnapshot 时引用固定策略内容，在该配置下一轮装载本地包。 | 关闭 RSI 后新开指定配置轮次，实际策略与本地摘要匹配，在途轮次及另一配置仍使用各自旧快照。（AC19、AC21） | TODO |
| T412 · 接入显式选用操作 | T407、T410、T411 | `src/pages/CodingSettings.tsx`、`src/lib/coding.ts`、`src/i18n/types.ts`、`src/i18n/zh.ts`、`src/i18n/en.ts`。在指定 Coding 配置中显示待选版本、证据与摘要，保存后展示生效修订及下一轮版本。补齐候选与 Harness 的双语反馈。 | 在两个配置之间切换，确认只对选定配置生效。不可验证版本在界面可见原因。（AC21） | TODO |
| T413 · 验证两侧并发与独立启动 | T406、T408、T412 | `webdemo/tests/test_exchange.py`、`webdemo/tests/browser.py`。并发运行两个普通仓库和一个 RSI 实验，分别取消、独立启动与恢复。再交叉校验候选材料与 Harness。 | 从三组 ID 对照消息、目录、配置、权限与工具结果。取消一个其余仍推进，无普通会话时实验也能完成。（AC19、AC20、AC21） | TODO |
| T414 · 验收第四阶段 | 本阶段全部前项 | `webdemo/tests/test_exchange.py`、`webdemo/tests/api_smoke.py`、`docs/redesign/task.md`、`docs/redesign/checklist.md`。在两个跨模块入口重送请求，测试私测及凭据标记，收集独立副本、本地绑定及冻结题集的核对结果。 | AC19–AC21 与 AC22–AC24 的跨模块部分均有运行证据。记录候选 ID、清单摘要、指定配置修订和固定 Harness。（AC19、AC20、AC21、AC22、AC23、AC24） | TODO |

## Phase 5 · 完整回归与最终演示

### 构建、回归与故障验证

| 任务与目标 | 直接依赖 | 涉及文件与实施内容 | 测试操作与通过条件 | 状态 |
| --- | --- | --- | --- | --- |
| T501 · 准备最终回归环境 | T101 | `webdemo/tests/recovery.py`、`webdemo/tests/api_smoke.py`。在独立仓库与服务数据目录准备含已知失败测试的项目、只读归档副本及三种可区分的运行配置，装入 T101 基线。 | 核对目录、端口与基线摘要均指向验收样本，两种产品入口能分别查询。（AC19、AC26） | TODO |
| T502 · 运行前端构建 | T501 | `frontend/package.json`。在项目根执行 npm --prefix webdemo/frontend run build。 | 类型检查和构建均以零退出，打开构建产物对应的 Coding 与 RSI 路由能加载组件。（AC01、AC14） | TODO |
| T503 · 运行后端与引擎回归 | T501 | `webdemo/tests/test_coding_lifecycle.py`、`webdemo/tests/test_exchange.py`、`tests/rsi/test_day4.py`。运行 `.venv/bin/python -B -m pytest -q tests`，再显式运行两个新增的 Web 单测文件，保存实际结果及跳过原因。 | 会话竞争、版本事务、冻结输入、跨库重试与配置隔离用例均执行通过，所需用例若跳过则补跑并保存结果。（AC06、AC16、AC17、AC22） | TODO |
| T504 · 运行既有实服务场景 | T501、T502 | `webdemo/tests/api_smoke.py`、`webdemo/tests/browser.py`、`webdemo/tests/recovery.py`。执行 API 冒烟、旧 RSI 浏览器场景、已有真实 RSI 单题场景及恢复脚本，按各自来源保存运行 ID。 | 旧链接可访问原作业，公开示例、评分回执、拒绝历史与中断清理分别能从对应场景复核。（AC14、AC15、AC16、AC18、AC26） | TODO |
| T505 · 验证六种幂等操作 | T501、T503 | `webdemo/tests/test_coding_lifecycle.py`、`webdemo/tests/test_exchange.py`、`webdemo/tests/api_smoke.py`。对提交轮次、权限答复、取消、启动实验、主动回滚、提交候选分别模拟双击和成功应答丢失，再让完成与取消竞争。 | 分别核对一次副作用、原回执及唯一终态，双标签页刷新与持久记录相符。（AC22） | TODO |
| T506 · 逐入口核对路径范围 | T501 | `webdemo/tests/test_coding_lifecycle.py`、`webdemo/tests/test_exchange.py`、`webdemo/tests/api_smoke.py`。在临时目录通过受限链接测试文件浏览、搜索、新建、编辑、删除、Diff、Shell、实验公开轨迹及候选导出。 | 受限标记未被展示或导出，受限目标目录清单与内容摘要相同。同一组正常路径的操作成功。（AC23） | TODO |
| T507 · 核对凭据传播范围 | T501 | `webdemo/tests/api_smoke.py`、`webdemo/tests/test_exchange.py`、`webdemo/tests/browser.py`。用本地测试 Provider 的可识别凭据各做一次成功和错误调用，查浏览器存储、配置、日志、事件、会话、产物、导出、Shell 与测试进程。 | 仅目标 Provider 确认收到标记，其他检查位置匹配零次。报告只写位置和计数。（AC24） | TODO |
| T508 · 区分故障并保留证据 | T501、T503 | `webdemo/tests/recovery.py`、`webdemo/tests/browser.py`。依次制造断线、Provider 失败、权限拒绝、命令失败、评分设施不可用及进度保存失败。 | 页面均显示受影响的运行、已有证据和恢复方式。写库失效期间没有新命令，缺失测试、分数、费用如实标出。（AC25） | TODO |
| T509 · 复核长任务及取消 | T501、T502 | `webdemo/tests/browser.py`、`webdemo/tests/recovery.py`。执行普通会话的 coding_long 场景，将同一轮服务端起止时间、离线输出与子进程退出记录关联。 | 运行实际满 1800 秒，关闭所有页面再打开后可读原进度，从 Web 取消，工作树在清理完成后可接手。（AC07、AC13） | TODO |

### 原生兼容与跨模块演示

| 任务与目标 | 直接依赖 | 涉及文件与实施内容 | 测试操作与通过条件 | 状态 |
| --- | --- | --- | --- | --- |
| T510 · 验收 CLI 与 TUI | T501 | `mewcode/__main__.py`、`mewcode/app.py`。从临时项目用 CLI 的 `-p` 与 `--output-format stream-json` 发起真实工具请求，再于 tmux 启动原 TUI，打开升级前可恢复的会话副本并续问。 | 捕获工具实际结果和续聊内容，与 T101 已知事实相符。原生会话路径及格式仍可读取。（AC26） | TODO |
| T511 · 执行最终演示的 Coding 部分 | T501、T502 | `webdemo/tests/browser.py`。增加并运行同一浏览器演示场景 coding_rsi_roundtrip。先从普通入口使用真实模型完成解释、修复、重跑和保存，保留会话 ID。 | 关闭重开并续问，记录真实模型、配对调用、测试先败后过与 Git Diff。在磁盘上独立重跑成功。（AC02、AC27） | TODO |
| T512 · 执行最终演示的 RSI 部分 | T511 | `webdemo/tests/browser.py`。在同一演示中进入 RSI，选冻结题集、被测模型及 Harness，启动进化，查看失败轨迹、策略修改、版本分数、回执与决定，再返回原 Coding 会话。 | 实验 ID 能追到评分和版本证据，返回的原会话 ID、目录和配置相同，下一轮仍能引用刚才的修改。（AC16、AC21、AC27） | TODO |
| T513 · 比对升级前后的历史材料 | T501、T504 | `catalog.py`、`webdemo/tests/recovery.py`。按 T101 保存的原标识逐一打开归档，核对轨迹、回执、策略快照、代码产物、分数、版本关系和真实或演示来源。 | 读前读后的归档清单、逐文件摘要及历史值与基线一致。只读归档无数据库升级或新增文件。（AC18、AC26） | TODO |
| T514 · 汇总五阶段实测结果 | 本阶段全部前项 | `docs/redesign/task.md`、`docs/redesign/checklist.md`。依次核对 AC01–AC27 的同次运行证据以及 F1–F24、N1–N13 映射。记录真实模型、实际执行结果、遗留项及复测结果。 | 每条 AC 有可复核标识与实际通过结论。缺证据或失败项保留状态，处理并复测后才把相应任务标为 DONE。（AC27） | TODO |

## 验收标准与任务索引

编号及需求归属沿用已批准的规格。下表给出取证任务。通过结论在实际执行时填写。

| 验收标准 | 对应需求 | 主要取证任务 |
| --- | --- | --- |
| AC01 | F1、F2 | T138 |
| AC02 | F3、F4、F5、F7、F11 | T224、T511 |
| AC03 | F4、F7 | T138 |
| AC04 | F5、F6 | T138 |
| AC05 | F8、N9 | T138、T221 |
| AC06 | F9、N7 | T138、T221 |
| AC07 | F10、N4 | T203、T204、T509 |
| AC08 | F6、F11、N2 | T207、T222 |
| AC09 | F11、F14、N5 | T211、T222 |
| AC10 | F10、F11、N5 | T209、T222 |
| AC11 | F12 | T213、T214、T227 |
| AC12 | F13 | T215、T216、T217、T227 |
| AC13 | N1、F10 | T225、T226、T509 |
| AC14 | F15、F16 | T310、T323、T406 |
| AC15 | F17 | T308、T309、T323 |
| AC16 | F18、F19 | T313、T314、T321、T512 |
| AC17 | F20、N12 | T318、T319、T320、T323 |
| AC18 | F21 | T322、T513 |
| AC19 | F22、N6 | T223、T413 |
| AC20 | F23 | T406、T414 |
| AC21 | F24 | T408、T411、T413 |
| AC22 | N3 | T505 |
| AC23 | N8 | T506 |
| AC24 | N10 | T507 |
| AC25 | N11 | T508 |
| AC26 | N13、F22 | T101、T510、T513 |
| AC27 | F3、F11、F16、F18、F19、F22 | T511、T512 |
