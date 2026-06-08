# OpenRelix 隐私威胁模型

> 语言版本：[English](privacy-threat-model.md) | 简体中文

这份文档用于贡献者、maintainers 和 connector 作者在设计改动时检查隐私边界。它不替代法律审查，也不应该包含真实用户数据。

## 保护目标

OpenRelix 应保护：

- 原始 AI host activity：prompts、conclusions、session metadata、source paths。
- runtime state：`raw/`、`consolidated/`、`registry/`、`reports/`、`runtime/`、`log/`。
- host-native files：Codex 和 Claude Code 的配置、memory、session、skill roots。
- 外部 connector 内容：Feishu、calendar、mail、docs、meeting notes、issue trackers 等。
- release artifacts：npm package、GitHub Pages、screenshots、fixtures、examples、changelogs。

公开 repo 只能包含可复用逻辑、脱敏示例、模板、测试和文档。

## 信任边界

| 边界 | 风险 | 处理方式 |
| --- | --- | --- |
| Repo source -> external state root | 用户数据误进 repo | state root 默认在 repo 外；生成目录被 ignore；fixture 必须合成 |
| Host home -> OpenRelix state | host transcript 或 config 泄漏 | 采集器只写本地 state；公开输出必须脱敏 |
| OpenRelix state -> host context | registry 被完整注入 host memory | 只写 bounded summary 和 managed block |
| Local docs -> Feishu/cloud docs | 内部链接、路径、账号或日志被复制到云端 | 只上传公开版，飞书文档使用脱敏内容 |
| Repo -> npm/GitHub Pages | package 或站点带出私有素材 | 发布前检查 package list、docs、assets 和 screenshots |
| Connector -> repo docs/tests | 第三方内容成为 fixture | fixture 使用 synthetic data，不使用真实消息或文档 |

## 主要威胁

### 1. 原始历史泄漏

风险：把 AI host transcript、session file、raw window、private screenshot 或原始日志提交进 repo。

控制：

- 不提交 `raw/`、`reports/`、`runtime/`、`log/`。
- 测试用 `tests/fixtures/sample-state/` 的合成数据。
- 文档只写通用结构和脱敏字段。
- 发布前运行 `python3 scripts/check_personal_info.py`。

### 2. 真实路径和账号泄漏

风险：公开 docs 或 tests 带出真实 home path、账号、组织名、内部 URL 或机密标识。

控制：

- 用 `<state-root>`、`CODEX_HOME`、`/tmp/openrelix-demo`、`/workspace/sample-project` 这类占位符。
- 不在 repo 中保存真实 Feishu、mail、calendar、meeting 或 issue 内容。
- 不粘贴完整未脱敏错误日志。

### 3. Host Context 过量注入

风险：把完整 local registry、raw windows 或低优先级/本地-only memories 写进 Codex/Claude context。

控制：

- `registry/memory_entries.jsonl` 是源头，host context 是编译产物。
- 只有 `global_context` 和 `project_context` 进入 bounded summary。
- `local_only`、`on_demand`、`never` 保留在本地检索层。
- sync 只更新 OpenRelix managed block，不覆盖 host-owned content。

### 4. Connector 数据外流

风险：飞书、日历、会议、邮件或其他 SaaS connector 的原始数据进入 repo、npm package 或第三方 cloud memory。

控制：

- Connector 输出先转成公开摘要或脱敏字段。
- 涉及第三方云记忆或 SaaS 持久化前，先确认合规边界。
- 公开文档中只保留流程、字段形态、验证路径和结论。

### 5. 产品埋点外发

风险：为了判断 DAU、模块停留和产品卡点，把本地工作内容或身份信息随埋点发到外部服务。

控制：

- 只有配置了 `OPENRELIX_ANALYTICS_ENDPOINT` 的 macOS panel client 会联网发送埋点；该产品路径默认开启。
- 用户可以在 OpenRelix 菜单中关闭，也可以通过 `OPENRELIX_ANALYTICS_ENABLED=0` 或 `OPENRELIX_ANALYTICS_DISABLED=1` 启动关闭。
- 允许字段仅限随机 install ID、单次启动 session ID、app 版本、粗粒度 macOS 版本、固定事件名、白名单模块/控件 ID、停留毫秒数和 panel 语言。
- 禁止发送 prompt、模型回答、memory/review 正文、窗口标题、项目名、文件路径、用户名、主机名、token、Cookie、connector 内容、本地报告、raw state 或生成后的 panel 内容。
- 客户端发送前必须丢弃未知事件和未知属性，失败的埋点 payload 不落盘。
- 嵌入 macOS app 的 token 只能视为客户端 ingest token，不能当服务端密钥；高权限 analytics 凭证必须留在采集 endpoint 后面。

### 6. Release Surface 扩大

风险：package allowlist 或站点资源扩大后，带出开发 harness、fixture、私有素材或生成报告。

控制：

- `npm pack --dry-run --json` 检查文件列表。
- 不把开发用 harness skills 加进 `plugins/openrelix/` 或 package `files`，除非有明确 release 决策。
- `.github/`、`tests/fixtures/`、真实 runtime state 不进入 npm 包。

## 贡献者清单

提交前确认：

- 我没有提交真实 host history、logs、runtime reports 或账号信息。
- 新 fixture 是合成数据，且字段足以验证行为。
- 新 docs 同时有中英版本或已在 bilingual HTML 页面内提供语言切换。
- 任何公开截图都不含真实用户数据、路径、组织名或内部内容。
- package dry run 不包含不该发布的文件。
- host context 改动不会写出 OpenRelix managed block 之外的内容。

## 设计建议

新输出应该尽量是两类之一：

- source-of-truth record，有明确 owner、schema、写入路径和隐私级别。
- rebuildable artifact，可以从 source records 重建，删除不会损坏源数据。

模糊 cache 最容易产生隐私问题。新增 cache 时，必须说明它的来源、生命周期、是否可重建、是否可能进入 package 或 cloud docs。
