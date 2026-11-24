# Helix RSI 核心引擎与运行时

此目录保存 Helix Harness 的核心 Python Agent 运行时、单仓库自进化（Per-Repo RSI）引擎、冻结评测基准（`evaluation/`）及回归测试套件（`tests/`）。

内部运行时包源自早期 `mewcode` Python 运行时快照（0.2.0），保留既有作者与来源标注及内部包名，以确保既有冻结基准指纹（`freeze.lock.json` 与 `judge-v1.json`）跨检出完全可复算验证。产品层的 Web API、Coding Workspace 与前端适配位于根目录及 `helix_harness/`。

验证冻结基准指纹可在仓库根目录执行：

```sh
uv run --frozen python -m mewcode.rsi.preflight verify
```

前端独立使用 PenguinHarness 发布的共享组件包 `@prismshadow/penguin-ui@0.2.18`，由 npm 锁文件按完整性校验安装。[上游代码](https://github.com/Prism-Shadow/penguin-harness)
