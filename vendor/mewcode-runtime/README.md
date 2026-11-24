# MewCode 运行时来源

此目录从项目拆分当日使用的 mewcode-python 工作副本导入。Python 包版本为 0.2.0。源码保留了原有的作者和来源标注。mewcode/rsi、evaluation 和 tests 一并保存，以便重放既有实验并验证冻结输入。此快照中没有随附顶层许可证文件。

运行时指纹同时覆盖原生包、原 pyproject.toml 与原 uv.lock。产品层的配置、依赖与适配放在仓库根目录及 helix_harness 中。验证指纹可在仓库根目录执行以下命令。

```sh
uv run --frozen python -c 'from mewcode.rsi.runtime import ROOT; from mewcode.rsi.fixtures import verify_lock; print(verify_lock(ROOT)["runtime_digest"])'
```

前端独立使用 PenguinHarness 发布的共享组件包 `@prismshadow/penguin-ui@0.2.18`。包由 npm 锁文件按完整性校验安装，许可证随包分发。[上游代码](https://github.com/Prism-Shadow/penguin-harness)
