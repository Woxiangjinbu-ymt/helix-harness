# Coding Workspace 会话工具栏与运行信息改造提案

2026-11-24 · 已批准并实施。目标项目 MewCode 基线 `22f98b6`，参考项目 PenguinHarness `f6d40afb5`，共享 UI 包版本 `0.2.18`。

## 设计决定

Coding 首页和已有会话使用同一个 PenguinHarness `ComposerCard`。首页居中，有消息后放在记录底部。左侧放添加附件、权限、技能，右侧放上下文占用、思考强度、模型、发送或停止。工作目录保留在输入框下方或已有会话的目录入口。模型配置和密钥编辑仍在设置页，输入框只选择可用配置。默认权限取 MewCode 的实际配置。历史回复显示该轮实际使用的模型。

直接采用 `@prismshadow/penguin-ui@0.2.18` 的展示组件。MewCode 的 Web 层负责读取模型目录、管理草稿和提交轮次，Python 会话管理器继续负责快照、授权、写入权、取消和事件续读。PenguinHarness 的整套 ChatInput 同时处理跨 Agent 切换、队列、机器路由等业务。它的核心 Session 以 AMSP 为输入输出，依赖自己的上下文引擎和模型调用层。引入该 Session 意味着重新决定谁拥有历史、授权和文件操作，属于另一种运行时架构。[共享输入框](https://github.com/Prism-Shadow/penguin-harness/blob/f6d40afb555008b952b7b23a0ebf59ae338e5fcf/packages/ui/src/components/chat/composer/composer.tsx) · [PenguinHarness 输入栏装配](https://github.com/Prism-Shadow/penguin-harness/blob/f6d40afb555008b952b7b23a0ebf59ae338e5fcf/packages/web/src/features/chat/chat-input.tsx) · [PenguinHarness Session](https://github.com/Prism-Shadow/penguin-harness/blob/f6d40afb555008b952b7b23a0ebf59ae338e5fcf/packages/core/src/session.ts) · [MewCode Web Runtime](../../helix_harness/coding/runtime_adapter.py)

## 能力与可复用代码

| 能力 | MewCode 现状 | 改造与复用 |
| --- | --- | --- |
| 模型切换 | 配置列表、设置页选择、会话绑定和轮次快照均已实现。首页输入框只读取浏览器保存的配置来创建会话。 | 直接用共享包的 `ModelSelect`，用现有 `GET /api/coding/profiles` 填充选择菜单。明确区分提供方、模型、配置显示名和凭据可用状态。首次选择随建会话提交，已有会话从下一轮生效。 |
| 思考强度 | 原生 Provider 有布尔 `thinking`，Anthropic 客户端有对应请求分支。OpenAI 系两条客户端有推理输出处理，当前请求构造没有通用档位。Web 自建配置生成快照时固定 `thinking=False`。 | 用共享 `MenuSelect` 和 PenguinHarness 思考菜单的交互，增加后端能力描述、所选档位和协议映射。按具体配置返回可选项。只支持开关的显示开关，未确认的只显示模型默认值。 |
| 上下文窗口 | 原生已有 `get_context_window()`、`current_tokens()`、缓存用量锚点和压缩阈值。Web 已发出 `usage`、`compact`，现有 `UsageEvent` 是本轮 Agent 实例内多次请求的累计值。 | 直接用 `ContextRing`，详情显示当前上下文估算、模型窗口、自动压缩阈值和读数来源。若按“上下文占用”命名，百分比用当前用量除以模型窗口。PenguinHarness 的环默认衡量距压缩阈值的距离，复用组件时需显式选分母。 |
| 项目文件与上传 | 工作树内已有文件浏览、搜索和读取。Web 的 `TurnRequest` 只有文本和提交号，原生用户消息正文也是字符串。工具结果已有部分结构化块，其用途与用户图片不同。 | 直接用 `AttachmentList`、`AttachmentFile`、`AttachmentImage`。将“引用项目文件”和“上传外部文件”作为两个操作。外部附件存入会话私有目录，由后端按附件 ID 读取。图片还需扩展用户消息、历史恢复和协议序列化。 |
| 权限、技能和回复详情 | 已有逐调用审批、配置权限模式、会话扩展选择和调用记录。普通回复目前按文本展示。 | 把当前权限和已启用技能接入工具栏。复用共享回复或 Markdown 展示组件处理列表、代码和表格。授权卡与终端输出继续按调用 ID 关联，Diff 展示工作树实际变更。先展示有依据的用量和耗时，再接入有价格来源的估算费用。 |

代码依据见 [配置列表与快照](../../helix_harness/coding/profiles.py)、[现有设置页](../../helix_harness/frontend/src/pages/CodingSettings.tsx)、[轮次请求](../../helix_harness/coding/models.py)、[三种模型请求构造](../../vendor/mewcode-runtime/mewcode/client.py)、[消息序列化](../../vendor/mewcode-runtime/mewcode/serialization.py)、[当前上下文估算](../../vendor/mewcode-runtime/mewcode/conversation.py)、[PenguinHarness 用量环口径](https://github.com/Prism-Shadow/penguin-harness/blob/f6d40afb555008b952b7b23a0ebf59ae338e5fcf/packages/web/src/features/chat/context-gauge.tsx) 和 [上传实现](https://github.com/Prism-Shadow/penguin-harness/blob/f6d40afb555008b952b7b23a0ebf59ae338e5fcf/packages/server/src/services/task-attachments.ts)。

## 前后端契约

1. **模型能力和下一轮设置。** 为可用配置补充模型窗口及来源、已验证的思考档位、图像输入能力。客户端只传配置标识和选择，不接触密钥。已有设置接口会同时覆盖技能、MCP 和 Hooks；增加按字段更新的会话选项接口，或在提交轮次时携带明确的覆盖字段。建议以提交事务为最终边界，在同一回执中返回实际快照标识。工具栏切换模型只采用模型相关字段，当前权限策略和扩展选择由各自显式操作更改。活跃轮次继续使用已冻结的模型、推理和权限配置。
2. **附件与幂等。** 草稿支持选择、拖入、粘贴图片和移除。首次发送先绑定目录、再将附件上传到该会话；上传失败保留草稿和可重试的附件状态。已上传附件返回服务端 ID、显示名、类型和大小。`POST /api/coding/sessions/{sid}/turns` 保留旧文本格式并接受有序附件引用，允许只有附件的消息。每次重送使用原提交号；幂等比较覆盖文本、附件及本次显式选择，已有回执始终指向原轮。浏览器重开后可恢复已暂存的附件引用；未持久化的浏览器本地文件应提示重新选择。清理未提交的暂存内容时保留已归属历史的附件。
3. **文件进入模型的方式。** 工作树内的引用走现有路径和读取权限。外部文本附件提供受限、只读的会话附件读取，或在可控大小内作为明确标识的用户材料送入模型。校验会话归属、文件类型、单文件及总量上限，同名文件分别登记。当前 `ReadFile` 有工作目录边界，直接把私有附件的绝对路径写进提示词会得到不可读引用。图片通过支持该能力的 Provider 发送结构化内容；切换到无法处理该图像的模型时明确提示。PDF、Office 等格式按已实现的提取能力开放。
4. **上下文和历史。** 在 Runtime 的用量锚点更新和压缩边界处产生上下文读数，附带对应轮次、模型、窗口、阈值及估算或实测来源。由现有事件存储与 SSE 分发，重连从同一视图和游标恢复。模型切换后采用新窗口重新估算，压缩后尚无新测量时显示估算或待测。轮次累计量取该轮最新累计读数；会话累计按轮汇总。耗时使用已存的开始和结束时间，若展示生成速率则单独测量生成时段。缺失用量或价格时显示未知。
5. **兼容和样式。** 旧的纯文本轮次和历史数据按原格式恢复；新增字段可缺省。预编译样式包含全局 reset，MewCode 与 PenguinHarness 使用的主题属性也有差异。第一批在 Coding 独立的样式范围或文档入口装配主题、浮层和工具提示，核对跳转 RSI 后的样式。优先使用固定版本的发布包。若实际首屏开销不可接受，再从固定提交抽出少数组件及必要依赖，并保留许可证、上游来源和相关声明。[共享包接入说明](https://github.com/Prism-Shadow/penguin-harness/blob/f6d40afb555008b952b7b23a0ebf59ae338e5fcf/packages/ui/README.md) · [当前提交与回执](../../helix_harness/coding/store.py) · [现有事件续读](../../helix_harness/frontend/src/lib/coding.ts)

## 交付顺序与通过条件

| 批次 | 交付 | 可观察的通过条件 |
| --- | --- | --- |
| 1 · 输入栏与模型 | 复用输入框、模型选择、发送与停止。接入目录、技能和权限入口，完善回复排版。 | 同一会话先选 A 后选 B，捕获两轮实际模型请求并核对快照。修改下一轮选项不改变运行中的 A。模拟保存失败、丢回执及双击，草稿可恢复且只产生一轮。切模型保留权限与技能选择。用中文输入法、窄屏、深浅色和 RSI 往返核对操作与样式，并与现有首屏比较加载量。 |
| 2 · 上下文与思考 | 读数事件、窗口详情、按模型呈现的思考选项、轮次用量及耗时。 | 在有多次模型调用的轮次核对当前占用与累计量。模拟压缩、断线、刷新和小窗口模型切换，读数口径保持一致。捕获实际出站请求核对所支持的两个档位，至少在一个真实配置上验证；未知能力显示默认值。 |
| 3 · 附件 | 先交付项目引用与外部文本，再交付粘贴、拖入和上传图片。 | 上传含唯一标记的文本，模型能读出标记。给视觉模型一张只能从像素判断内容的测试图，核对请求中的图像块与回答。覆盖同名文件、超限、附件单独发送、跨会话引用拒绝、失败重试和重开历史。上传后工作树的 Git Diff 保持原样。 |

## 已完成的可行性验证

使用与 MewCode 安装一致的 React / React DOM 19.3.1、Vite 6.4.5，在临时工程安装发布包 `@prismshadow/penguin-ui@0.2.18`。导入 `ComposerCard`、`ModelSelect`、`ContextRing`、`SendButton`、`AttachmentList`、`AttachmentFile` 和预编译样式，生产构建通过。Chrome 显示附件和上下文环，点击模型按钮触发宿主回调，输入并发送后得到切换后的模型标识与文本。浏览器未报告页面脚本错误。

该独立页面首屏实测解码后 JS 为 1,040,423 字节，CSS 为 186,072 字节，包含 React 和测试宿主。Vite 提示存在超过 500 kB 的分块，当前 Node 18 安装部分传递依赖时产生引擎版本提示。这些数值用于指导在正式 Coding 页做加载与兼容性对照。模型请求、附件上传和运行中切换的端到端验收安排在上表对应批次。

## 实施与验收记录

Coding 首页和已有会话共用发布包中的输入框、菜单、模型按钮、上下文环、附件和发送控件，回复使用共享 Markdown 组件。选项随轮次事务冻结，服务端回执包含快照 ID。Coding Worker 在自己的模型客户端上适配协议。RSI 继续校验原发布摘要。附件存于会话私有 SQLite，草稿文件暂存在浏览器 IndexedDB，用户目录中不会生成上传文件。[输入栏实现](../../helix_harness/frontend/src/components/CodingComposer.tsx) · [协议与历史适配](../../helix_harness/coding/model_input.py) · [附件存储](../../helix_harness/coding/attachments.py)

- Chrome 在同一会话运行中切换下一轮模型，核对两轮的实际结果。HTTP 回归另捕获两个模型的出站请求，并核对权限保持、同一提交号重放及跨会话附件拒绝。
- 真实 `deepseek-official / deepseek-flash` 验证关闭、低、高三个档位，读出文本附件中的随机标记，识别粘贴图片的颜色，刷新后可再次回答。附件草稿在关闭并新开标签页后可以发送。协议字段与视觉能力参考 [DeepSeek 思考模式](https://api-docs.deepseek.com/guides/thinking_mode/) 和 [DeepSeek 视觉输入](https://api-docs.deepseek.com/guides/vision/)。
- 两次模型请求的受控用量测试中，轮次累计输入 260、输出 15，末次上下文为 165。界面分别展示当前上下文、窗口来源、压缩阈值、轮次及会话用量。缺少提供方用量时保留估算来源。真实付费模型验收限于上述 DeepSeek 配置。
- 生产构建对照使用同版依赖、空会话和独立 Chrome 页面。基线 Coding 首页加载约 0.79 MB JS 和 0.05 MB CSS，接入后分别约为 1.64 MB 和 0.24 MB，均为解码大小。共享组件按 Coding 路由加载，RSI 往返、390 像素窄屏与主题切换通过浏览器检查。
- 本轮开放 UTF-8 文本以及 PNG、JPEG、WebP 图片。每轮最多 8 个附件、合计 8 MiB。单个文本最多 64 KiB，单张图片最多 4 MiB。PDF 和 Office 内容提取留待后续。

可重复运行的用例位于 [Coding 接口与 Runtime 测试](../../helix_harness/tests/test_coding_lifecycle.py) 和 [Chrome 验收](../../helix_harness/tests/browser.py)。浏览器场景 `coding_composer_controls` 使用受控模型，`coding_composer_real` 使用配置好的 DeepSeek 服务。
