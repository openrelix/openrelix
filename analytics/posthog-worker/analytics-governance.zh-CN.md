# OpenRelix macOS 面板埋点治理

这份约束用于 AI 或人工开发 OpenRelix macOS 面板功能时，同步设计、开发和维护埋点表格与 PostHog 看板。目标不是多采集数据，而是让每个新增模块、按钮和核心路径都有可解释、可复用、可验证的产品指标。

## 维护目标

- 每个面板模块都能回答：有没有人看、看了多久、是否带来后续动作。
- 每个核心按钮都能回答：有没有人点、从哪个模块点、是否说明功能有价值或有阻塞。
- 每张看板卡片都能回答一个产品问题，避免只有事件堆叠。
- 默认只上报匿名、非敏感、白名单内的产品使用信号。

## Source of Truth

- 采集入口：macOS client 的面板埋点桥接逻辑。
- 清洗与白名单：`analytics/posthog-worker/worker.mjs`。
- 事件表与看板维护约束：本文档。
- 产品看板：PostHog 中文产品看板优先使用 `module_label_zh` / `control_label_zh` 展示；英文 raw ID 只用于调试看板。

## AI 开发埋点流程

1. 设计功能时先写产品问题。
   - 示例：用户是否会打开“个人资产记忆”？打开后是否停留足够久？是否点击反馈按钮？
2. 设计埋点表。
   - 选择已有事件，优先复用 `module_visible`、`module_hidden`、`control_click`、`panel_*`、`app_*`。
   - 新增 `module_id` / `control_id` 时必须同时给中文展示名。
3. 开发采集。
   - 面板侧只发送稳定 ID、停留时长、原因、语言等固定字段。
   - Worker 侧只允许白名单事件和属性通过。
4. 维护看板。
   - 中文产品看板用 `module_label_zh` 或 `control_label_zh` 做 breakdown。
   - Raw debug 看板可以用 `module_id` / `control_id`。
5. 验证闭环。
   - 单测覆盖事件是否被接收、敏感字段是否被丢弃、中文标签是否补齐。
   - 有真实 PostHog 权限时，触发一次测试事件并确认看板字段出现。

## 埋点表模板

| 产品问题 | 事件名 | 触发时机 | `module_id` | `module_label_zh` | `control_id` | `control_label_zh` | 允许属性 | 禁止属性检查 | 看板卡片 | 测试用例 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 用户是否使用某模块 | `module_visible` / `module_hidden` | 模块进入/离开可见区 | stable snake_case ID | 中文模块名 | 空 | 空 | `dwell_ms`, `reason`, `panel_language` | 不含文本、路径、标题、用户名 | 模块浏览次数、模块停留时长 | Worker sanitization test |
| 用户是否点击某核心功能 | `control_click` | 点击按钮、菜单、筛选器或复制动作 | 所属模块 ID | 中文模块名 | stable snake_case ID | 中文按钮名 | `panel_language` | 不含输入内容、搜索词、复制内容 | 核心功能点击次数 | Worker label test |
| 用户是否完成启动 | `app_launch` / `panel_loaded` / `panel_ready` | 应用启动、WebView 加载、面板可交互 | 空 | 空 | 空 | 空 | app/coarse OS version | 不含 hostname、path、token | 启动到可用、DAU | Worker forwarding test |

新增功能时，AI 应在 PR 或提交说明里贴出新增/变更的表格行；如果没有新增埋点，也要说明复用了哪些已有事件和看板。

## 看板卡片模板

| 看板卡片 | 产品问题 | 事件与聚合 | Breakdown | 时间窗口 | 维护触发 |
| --- | --- | --- | --- | --- | --- |
| 模块浏览次数：按模块 | 哪些模块真的被看到 | `openrelix_module_visible` count | `module_label_zh` | Last 30 days | 新增/下线模块 |
| 模块平均停留时长：毫秒 | 哪些模块值得继续投入 | `openrelix_module_hidden` avg `dwell_ms` | `module_label_zh` | Last 30 days | 新增/重命名模块 |
| 核心功能点击次数：按按钮 | 哪些交互被使用 | `openrelix_control_click` count | `control_label_zh` | Last 30 days | 新增/下线按钮 |
| 启动到可用 | 是否存在启动或加载问题 | `openrelix_app_launch`, `openrelix_panel_loaded`, `openrelix_panel_ready` count | none | Last 30 days | 启动链路变更 |
| 面板加载失败次数 | 是否有加载失败或白屏风险 | `openrelix_panel_load_failed` count by `reason` | `reason` | Last 30 days | WebView/面板加载逻辑变更 |

PostHog 系统 UI 可能仍显示英文日期和系统提示，这不属于事件命名问题。产品卡片标题、breakdown 字段和中文标签应保持中文。

## 必须遵守的约束

- 不采集 prompts、模型回答、记忆正文、复盘正文、窗口标题、项目名、文件路径、用户名、hostname、token、cookie、本地报告内容或原始 OpenRelix state。
- `module_id` / `control_id` 一旦上线，不为了改文案而复用旧 ID 表达不同含义；语义变化明显时新增 ID。
- 新增允许的模块或按钮 ID，必须同时更新中文标签，否则 Worker 模块加载会失败。
- 中文产品看板不直接按 `module_id` / `control_id` 展示，除非是 raw debug 卡片。
- Dashboard 变更要和事件表同步：新增模块/按钮时更新对应卡片或明确复用已有卡片。
- 发送合成测试事件时，不使用真实用户 ID，不携带业务内容，并在说明中标记为测试样本。

## AI 提交前检查清单

- [ ] 是否写清楚新增功能对应的产品问题？
- [ ] 是否补了埋点表行，或说明复用现有事件？
- [ ] 是否更新了 `worker.mjs` 的 allowlist 与中文 label map？
- [ ] 是否补了 Worker 单测，覆盖事件清洗和中文 label？
- [ ] 是否确认敏感字段不会进入 PostHog？
- [ ] 是否给出中文看板卡片的创建/更新方式？
- [ ] 是否运行 `python3 scripts/check_personal_info.py`、`git diff --check` 和相关 focused tests？

## 推荐给 AI 的开发提示词

```text
实现这个 OpenRelix macOS 面板功能时，请同步设计埋点：
1. 先列出要回答的产品问题。
2. 复用或新增事件表行，包含 event、module_id/control_id、中文 label、允许属性、禁止属性。
3. 更新面板采集、Worker allowlist、中文 label map 和单测。
4. 说明 PostHog 中文看板需要新增或调整哪些卡片，breakdown 优先用 module_label_zh/control_label_zh。
5. 不上传任何用户输入、正文、路径、标题、账号、token、cookie 或本地状态。
```
