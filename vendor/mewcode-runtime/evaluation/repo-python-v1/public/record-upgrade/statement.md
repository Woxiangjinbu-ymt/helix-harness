# 旧档案升级

导入工具从标准输入逐行读 JSON，以 {"record": …} 接收一条档案，并逐行输出规范化后的档案。旧格式使用 name 和逗号分隔的 tags 字符串；新格式使用 id 和 labels 字符串列表。新字段存在时优先于同义的旧字段，即使列表为空。两种格式都要去除标签首尾空白，舍弃空标签，去重并排序。没有标签字段时输出空列表。有效输入至少含 id 或 name。

公开例子：输入 {"record":{"name":"sam","tags":"blue, blue, red"}}，输出 {"id":"sam","labels":["blue","red"]}。入口是 app.py，公开回归可用 python -m unittest public_checks 运行。
