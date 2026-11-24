# MTCode 运行时

`mtcode/` 是 Helix 随仓库维护的通用 Python Agent 运行时，源自 MewCode 0.2.0，保留原始作者和来源标注。命令行入口为 `mtcode`，Python 入口为 `python -m mtcode`。已有项目继续使用 `.mewcode/` 配置和 `MEWCODE.md` 指令文件。

从 Helix 根目录运行 `uv sync --frozen` 安装运行时，运行 `uv run --frozen python -m pytest vendor/mtcode-runtime/tests` 验证原生功能。Helix 的 [RSI 实现](../../helix_harness/rsi/) 与[冻结评测材料](../../evaluation/) 分别由产品包和评测目录维护。
