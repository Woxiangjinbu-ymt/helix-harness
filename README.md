# Helix Harness

Helix Harness 是本机运行的 Coding Agent 工作台。Coding Workspace 在选定目录中连续对话、调用工具和审阅修改。RSI Center 在独立实验工作区中执行评测、比较 Harness 版本和回滚。

## 本地启动

在仓库根目录执行以下命令。已在 macOS ARM64、Python 3.13 和 Node.js 24 上验证。需要 Git、uv 和 npm。

```sh
uv sync --frozen
npm --prefix helix_harness/frontend ci
```

在两个终端分别运行 `uv run --frozen helix-harness` 和 `./helix_harness/start-frontend.sh`。打开 [Coding Workspace](http://127.0.0.1:5173/#coding) 或 [RSI Center](http://127.0.0.1:5173/#overview)。独立 RSI 评分需要 Docker，镜像和端口配置见[操作手册](helix_harness/README.md)。

Coding 首页提供本地演示模型。使用真实模型时，可在 Coding 设置中添加配置。两个模块还可以读取 `~/Library/Application Support/Helix Harness/providers.yaml`，格式见[服务端配置示例](config/providers.example.yaml)。会话、附件、实验和凭据保存在仓库外。

## 代码与依赖

| 位置 | 内容 |
| --- | --- |
| [helix_harness](helix_harness/) | 产品 API、Coding 会话管理、工具授权、RSI 编排适配及后台工作进程 |
| [helix_harness/frontend](helix_harness/frontend/) | Coding 与 RSI 前端，通过 npm 引入锁定版本的 Penguin UI |
| [vendor/rsi](vendor/rsi/) | 核心 Agent 运行时、单仓库自进化（Per-Repo RSI）引擎、冻结评测与回归测试 |
| [docs/redesign](docs/redesign/) | 产品规格、设计、任务和历史验收记录 |

仓库内的相对路径依赖由 uv 和 npm 锁文件管理。检出本项目即可开发。更新运行时须重新验证冻结评测，依赖来源见[说明](vendor/rsi/README.md)。

## 验证

```sh
uv run --frozen python -B -m pytest -q
npm --prefix helix_harness/frontend run build
uv run --frozen python -m helix_harness.tests.browser --phase coding_onboarding
```
