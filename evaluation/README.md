# Repo Python v1 的冻结与校准

第 1 天冻结了三道仓库任务。每个 `public/<case>/` 含公开题面和可独立重建的 Git 仓库。出厂仓库的作者、提交时间、文件模式和 SHA-1 对象格式固定。私有检查及参考解位于 `private/<case>/`。任务容器只挂载当前题的临时公开工作树。宿主评分进程保留期望输出。校准成绩和第 3 天的正式 Agent 基线分别记录。

从 Helix Harness 仓库根目录执行。离线核验需要 Python ≥3.11 和 Git。

```sh
.venv/bin/python -m helix_harness.rsi.preflight verify
.venv/bin/python -m helix_harness.rsi.preflight checkout page-settings /tmp/mewcode-page-example
```

`checkout` 需要一个尚不存在的目标目录，返回完整的起始提交。题面位于 `repo-python-v1/public/page-settings/statement.md`。进入生成的仓库可运行 `python -m unittest public_checks`。

完整预检使用 Docker 的 Linux 容器，v1 固定为 `linux/arm64`。镜像首次下载后可离线重跑，运行时禁止自动拉取。

```sh
docker pull --platform linux/arm64 python@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
.venv/bin/python -m helix_harness.rsi.preflight check
.venv/bin/python -m pytest -q helix_harness/tests/rsi
```

预检检查非 root 身份、只读挂载、网络隔离、公开文件可读、私有哨兵不可读，并逐题运行公开回归和私有输入输出校准。每个私有样例等权计分。起始实现的预期分数依次为 25、40、25，参考解均为 100。此次实测记录在 `calibration/repo-python-v1.json`，含输入清单摘要、实际镜像 ID、每题起始提交及成绩。

`freeze.lock.json` 保存每题提交、题面和私测摘要，同时记录运行时、评测代码、安全配置、初始策略和参考解的摘要。校准前后均核对清单。源码树及发布清单属于维护者信任域，任何被测 Agent 或优化器只接收单独投影出的公开数据。需要更新题集时，维护者可用 `describe` 将当前输入的清单输出到临时文件，与已有清单比较并独立发布新版本。`verify` 和 `check` 均以已有清单为准。

## 运行一个编码 cell

从源码根运行以下入口。将配置路径和 Provider 名替换成受信配置里的实际值。运行目录位于项目外，由控制端持有。

```sh
.venv/bin/python -m helix_harness.rsi.runtime page-settings \
  --evaluation trial-01 --run-index 1 \
  --provider-config /path/to/trusted/config.yaml --provider my-provider \
  --runs-dir /path/to/rsi-runs
```

每个 cell 在独立 Python 进程内调用 `Agent.run()`，使用干净的公开工作树和临时用户目录。原生读、写、编辑、搜索工具操作该工作树。Bash 的公开测试入口为 `python -m unittest public_checks`，由固定的离线容器执行。容器只读挂载当前工作树，Provider 客户端和凭据留在受信进程。各项公开测试的输出返回给被测模型，正式评分使用独立的 Evaluator。

要比较策略，Python 调用方可在 `RunSpec` 同时提供 `strategy_dir` 与 `strategy_digest`。快照包含 `prompt.md`、`workflow.yaml` 和可选的 `skills/<name>/SKILL.md`。阶段可写为字符串，也可写为 `{name: verify, skills: [public-check]}`。阶段所选 Skill 的正文随初始指令加载，其余 inline Skills 可由模型通过 `LoadSkill` 加载。调用方可传 `expected_fingerprint` 固定比较条件。

运行目录以 `cell_id` 命名。`identity.json` 记录冻结条件、请求的 Provider 和模型；`events.jsonl` 记录按序的工具调用和配对结果、授权、模型输入摘要及累计用量；`artifact/` 封存任务仓库。最后发布的 `outcome.json` 给出终态、Session ID 和摘要。相同 cell 重试时核验并返回已封存的结果。原始模型正文、思考内容、工具输出和凭据均不进入公开轨迹。

`completed`、`turn_limit` 和 `timed_out` 保留完成或部分完成的仓库产物。`infrastructure_error`、`cancelled`、`worker_lost` 留待控制端处理。被强制终止的 worker 可留下供诊断的事件前缀，回执中的轨迹摘要为空。

## 建立和复算正式基线

从源码根目录运行。第 3 天使用 `default-v1` 和 `deepseek-official / deepseek-flash`。本机 Docker Desktop 29.4.1 通过 API 1.54 验收。以下第一条 Python 命令在新的状态目录中会调用模型，在本次已有的目录中会核验和复用三份回执。

```sh
state="$HOME/Library/Application Support/MewCode/RSI/day-3-baseline"
export DOCKER_API_VERSION=1.54
.venv/bin/python -B -m helix_harness.rsi.baseline baseline \
  --state-dir "$state" --evaluation day3-default-v1 \
  --provider-config .mewcode/config.yaml --provider deepseek-official
.venv/bin/python -B -m helix_harness.rsi.baseline rescore \
  --state-dir "$state" --evaluation day3-default-v1 --case page-settings
```

每道题固定运行一次。每份有效轨迹必须有同一 Session ID、连续事件序号、模型请求身份和终态。Evaluator 在独立解释器中核验题集、评分器版本、起始提交和封存摘要。每个私测使用新的只读、断网、非 root 容器，运行封存的 `app.py`。标准输入仅含本项输入，宿主比较输出。修改工作树中的公开测试不会改变计分规则。程序返回错误答案、非零退出、超时或缺少入口时，该项计零。轨迹丢失、Docker 设施故障或受保护输入漂移会使该题评测失败，并阻止发布。

每题按通过的私测比例计 0–100 分。总分为三题等权平均。耗时是编码阶段的单题平均时间。缺少可信计费数据时费用为 `null`。三题全部通过身份与完整性核验后，控制端才向 `control.sqlite` 提交正式记录，并导出到 `baselines/<evaluation_id>.json`。同一评测 ID 绑定同一计划，可以续评分并重建导出。更换模型或评分器需要新的评测 ID。

通过单题的 `runs[0].cell_id` 可以定位 `runs/<cell_id>/identity.json`、`events.jsonl`、`outcome.json` 和 `artifact/`。`rescore` 只读打开控制数据库，从封存产物重新执行私测，将完整回执与原件逐字段比较。逐项判分原因仅保存在 `evaluator-private/`。评分实现和题集的发布摘要见 `policy/judge-v1.json`。

本次基线 `day3-default-v1` 的三道题均为 100 分，编码耗时分别为 13.369 秒（availability-window）、15.907 秒（record-upgrade）和 12.851 秒（page-settings）。三次独立复算均重现原回执。在这个冻结题集上，后续策略的分数最高可持平。

## 第 4 天运行 Default RSI

从 MewCode 源码根目录运行。每个会话绑定一个正式基线、一条版本线、候选每题运行数和有效轮数上限。模型配置必须与该基线的被测 Runtime 匹配。可用 `--optimizer-provider` 单独指定同一受信配置中的提案模型，默认复用被测 Provider。

```sh
state="$HOME/Library/Application Support/MewCode/RSI/day-3-baseline"
.venv/bin/python -B -m helix_harness.rsi.orchestrator optimize \
  --state-dir "$state" --baseline day3-default-v1 --session day4-default \
  --provider-config .mewcode/config.yaml --provider deepseek-official \
  --target 100 --runs 2 --rounds 2
.venv/bin/python -B -m helix_harness.rsi.orchestrator inspect \
  --state-dir "$state" --session day4-default
```

`inspect` 从数据库同一读快照返回基线、正式榜、尝试记录、每次 Run 的 Session ID 和停止原因。加 `--scoreboard-yaml` 可只导出正式榜。当前正式基线已达到目标，所以上述调用保留 v1，候选运行数为零。

FailureAnalyzer 将低分与成功编辑、成功公开测试、工具错误和预算终态关联，并附上 Session 与事件序号。提案模型只能看到这些公开观测、题面、当前策略和本会话历史。它返回一份 JSON，引用父版本和证据，指定可测行为及预测方向，最多编辑八个策略文件。控制端检查路径、正文大小、题号、inline Skill 元数据和 Workflow 引用，并通过 Runtime 的装配器验证整份策略。策略文本的通用性还依赖提案质量，文件校验只能检查明确的语法和题目标识。

控制端将策略原子封存于 `objects/<sha256>/`。每个候选从当时的 Reference 快照派生，并分配新的版本号。每题先平均该题所有 Run，再对各题等权平均；候选成本只平均已知值，全未知时为 `null`。完整矩阵严格提分才晋升。预期行为是否出现另记为 `prediction.supported`。被拒候选留在 `attempts`，下轮使用保留的快照内容。

重新运行相同命令会核验策略、模型、矩阵和已有回执。尚未开始的 cell 可以启动；已登记的 cell 只从其原产物评分。产物缺失或身份不符时，本轮保持待处理，不消耗有效轮数。正式榜、Reference 和尝试结论在同一 SQLite 事务中提交。并发晋升若发现父版本已经改变，返回 `reference_changed`，让操作者检查该会话。

用以下命令复现可控模型验收。测试在临时控制库中运行真实 Agent Loop 和容器评分器，基线每题一次，三个候选每题各两次。预期依次为 v1 基线 30、v2 平分 30 被拒、v3 提分至 55 晋升、v4 降至 30 被拒。测试另外覆盖非法策略、残缺矩阵、写榜中断恢复和竞争晋升。

```sh
.venv/bin/python -B -m pytest -q helix_harness/tests/rsi/test_day4.py
```

## 第 5 天复现真实 Provider 验收

本次在独立数据目录 `day-5-real` 中从空库启动。模型为 `deepseek-official / deepseek-flash`，基线每题运行一次。候选预算为每题两次、最多两轮，目标为 100。重新使用以下 ID 会核验并复用已封存的记录。

```sh
state="$HOME/Library/Application Support/MewCode/RSI/day-5-real"
export DOCKER_API_VERSION=1.54
.venv/bin/python -B -m helix_harness.rsi.baseline baseline \
  --state-dir "$state" --evaluation day5-default-v1 \
  --provider-config .mewcode/config.yaml --provider deepseek-official
.venv/bin/python -B -m helix_harness.rsi.orchestrator optimize \
  --state-dir "$state" --baseline day5-default-v1 --session day5-default \
  --provider-config .mewcode/config.yaml --provider deepseek-official \
  --target 100 --runs 2 --rounds 2
```

要取回某次运行的任务仓库，按评测 ID、题目和从 1 开始的运行序号导出。该入口同样支持已评分候选的第 2 次及后续运行。导出的 ZIP 包含封存的完整文件树，应解压到新的空目录。JSON 回执中的 `archive_sha256` 用于核验 ZIP 字节，`artifact_digest` 用于核验解压后的仓库内容。原始提交号、轨迹和 Session ID 可以从同一条评分回执查到。

```sh
.venv/bin/python -B -m helix_harness.rsi.artifacts export \
  --state-dir "$state" --evaluation day5-default-v1 --case page-settings --run 1
.venv/bin/python -B -m helix_harness.rsi.artifacts rescore \
  --state-dir "$state" --evaluation day5-default-v1 --case page-settings --run 1
```

第 5 天三题均为 100 分，v1 达到目标后提前停止。三份仓库在空目录中还原后通过公开测试，独立 Evaluator 逐题复算得到与原件完全一致的回执。本机的验收报告位于 `$state/reports/day5.md`，逐题归档位于 `$state/exports/`。

## v1 归档与包迁移

原 `mewcode` 包迁移到 `vendor/mtcode-runtime/mtcode`，RSI 引擎迁移到 `helix_harness/rsi`。v1 的题目、起始提交、评分发布清单和已封存回执沿用原发布记录。`agent_id=mewcode` 是该版本的历史协议字段。

核验时先根据当前文件计算运行时、校准代码、裁判和执行适配器的实际摘要。[迁移记录](../helix_harness/rsi/relocation-v1.json) 将此次包迁移的四个精确构建摘要对应到迁移前提交 `d6288b9` 的发布身份。其他构建使用其自身的摘要，参与原有的漂移检查。未来调整 Agent 行为或评分逻辑时应发布新版本。可运行以下命令核对当前构建与历史裁判身份。

```sh
uv run --frozen python -c 'from helix_harness.rsi.evaluator import verified_release; print(verified_release()[1])'
```
