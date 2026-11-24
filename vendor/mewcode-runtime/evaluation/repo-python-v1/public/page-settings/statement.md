# 分页配置的优先顺序

导出命令从标准输入逐行读 JSON。每项请求提供 items，可选 settings.page_size 以及请求顶层的 limit。请使显式 limit 优先于配置值，两者都省略时使用默认 2。值为非负整数，0 表示空页。输出 items 保持原顺序，且只返回第一页。不更改现有输入输出格式。

公开例子：输入 {"items":[1,2,3,4],"settings":{"page_size":3},"limit":1}，输出 {"items":[1]}。入口是 app.py，公开回归可用 python -m unittest public_checks 运行。
