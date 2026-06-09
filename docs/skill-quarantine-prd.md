# Skill/MCP 小黑屋 PRD

> 语言版本：[English](skill-quarantine-prd.en.md) | 简体中文

版本: 1.0  
日期: 2026-06-05  
状态: 已进入本地实现

## 1. 背景

OpenRelix 会把可复用 Skills、MCP 与资产热度展示到工作台，也会把一部分资产作为候选上下文交给 Codex/Claude。随着本机安装的 Skills/MCP 变多，长期没有使用的项目仍然可能出现在默认展示或注入候选里，带来三个问题：

- 上下文噪音变高，真正高频使用的能力不够突出。
- 面板里的 Skill/MCP 列表膨胀，用户需要手动判断哪些仍有价值。
- 删除过于激进，用户希望先隔离，必要时再恢复。

小黑屋功能的目标是提供一个可逆、可审计、低风险的隔离层：不删除原始能力，只让默认展示和默认注入绕开它们。

## 2. 目标

- 基于近 30 天使用频率识别低频 Skill/MCP。
- 对新增不满 7 天的 Skill/MCP 归入可选隔离，不主动建议隔离。
- 支持用户手动隔离、手动恢复使用。
- 支持一键隔离所有建议项。
- 隔离后默认不注入、不在默认资产热度里出现。
- 不删除资产；可移动的全局 Skill 移入 runtime 隔离目录，可恢复；不可安全移动的项目做 state-only 隔离。
- MCP JSON 配置可从配置中移除并保存恢复 payload；Codex TOML MCP 可写入 `enabled = false` 并保存原始片段；未知格式做 state-only 隔离。

## 3. 非目标

- 不自动删除任何 Skill/MCP 文件。
- 不把小黑屋状态上传到第三方服务。
- 不修改仓库内 repo skill 的源文件位置。
- 不承诺识别所有 host 的私有 MCP 配置格式；未知格式只做状态隔离。

## 4. 用户故事

### 4.1 查看建议项

作为用户，我希望在 OpenRelix 工作台看到哪些 Skill/MCP 已经 30 天没有使用，从而快速判断是否需要隔离。

验收标准：

- 面板新增「Skill/MCP 小黑屋」区块。
- 区块显示建议隔离、小黑屋、可选隔离、活跃数量。
- 每一项显示类型、名称、30 天使用次数、最后使用时间、创建天数、原因。
- 新增不满 7 天且 30 天使用为 0 的项目显示为可选隔离，而不是建议隔离。

### 4.2 手动隔离

作为用户，我希望对单个 Skill/MCP 点击隔离，并立即从默认候选中隐藏。

验收标准：

- 每个非隔离项提供「隔离」动作。
- 隔离写入本地 runtime state。
- 全局可移动 Skill 被移动到 runtime 小黑屋目录。
- repo skill、未知位置 skill 不强行移动，写 state-only 记录。
- Codex TOML MCP 写入 `enabled = false`，同时保存原始 TOML 片段用于恢复。
- 操作后面板可刷新并看到隔离状态。
- 如果搬移、配置写入或恢复失败，面板保留隔离 entry 并展示迁移提示。

### 4.3 一键全部隔离

作为用户，我希望一键隔离所有建议项，也可以在确认后批量隔离可选项，减少逐个操作的成本。

验收标准：

- 面板提供「一键隔离建议项」和「一键隔离可选项」按钮。
- 建议项批量只处理 status 为 suggested 的项目；可选隔离批量只处理 status 为 grace 的项目。
- 可选隔离批量需要独立确认，表示用户主动覆盖 7 天保护期。
- API 返回本次隔离数量和明细。
- 操作失败时不影响已经成功隔离的项目。

### 4.4 手动恢复使用

作为用户，我希望从小黑屋恢复某个项目。

验收标准：

- 已隔离项提供「恢复使用」动作。
- 被移动的 Skill 恢复到原路径。
- 被隔离的 JSON MCP 配置恢复原 server payload。
- 被禁用的 TOML MCP 恢复隔离前保存的原始配置片段。
- 恢复冲突时保留小黑屋 entry，并显示失败状态，不覆盖用户现有文件。

## 5. 产品形态

### 5.1 工作台区块

入口位于左侧导航「资产层」附近，名称为「小黑屋」。区块包含：

- 顶部指标：建议隔离、小黑屋、可选隔离、活跃。
- 操作区：一键隔离建议项。
- 列表：按状态展示 Skill/MCP。
- 单项动作：隔离、恢复使用。

### 5.2 CLI

命令：

```bash
openrelix skill-quarantine list
openrelix skill-quarantine suggest
openrelix skill-quarantine blocked
openrelix skill-quarantine block skill:<id>
openrelix skill-quarantine unblock mcp:<id>
openrelix skill-quarantine block-all
openrelix skill-quarantine block-grace-all
```

所有命令支持 `--json` 供面板和自动化消费。

## 6. 规则

### 6.1 建议规则

- lookback: 30 天。
- grace: 7 天。
- Skill 使用次数来自 OpenRelix asset discovery/activation snapshot。
- MCP 使用次数来自 MCP usage view。
- `usage_30d > 0`: active。
- `usage_30d == 0` 且 `age_days < 7`: grace。
- `usage_30d == 0` 且不在 grace: suggested。
- 已在 state entries 中的项目: quarantined。

### 6.2 隔离规则

Skill：

- 全局用户 skill 位于已知安全根目录时，移动到 `{runtime_dir}/skill-mcp-quarantine/skills/`。
- repo 内 skill 不移动，只写 state-only，避免改仓库结构。
- 未知路径不移动，只写 state-only。

MCP：

- JSON 配置中的 server entry 可移除，并保存 `saved_config` 用于恢复。
- Codex TOML MCP 配置可写入 `enabled = false`，并保存 `saved_config_text` 用于恢复。
- 未知配置格式只写 state-only，不自动改写。
- 发现逻辑不展示 command args/env 等敏感配置内容；恢复 payload 仅保存在本地 runtime state。

### 6.3 硬化规则

- 面板、CLI 的隔离/恢复使用动作共享本地 runtime lock，避免同时搬目录或同时写配置。
- 修改 JSON/TOML MCP 配置前先写完整备份到 runtime 隔离目录。
- 每个隔离 target 记录迁移结果；`move_failed`、`config_failed`、`toml_failed`、`restore_conflict` 等状态会汇总为 `migration_warnings`。
- 面板在小黑屋展示迁移提示，单次点击也会即时显示提示数量。

### 6.4 过滤规则

- asset rows 在默认展示前过滤已隔离 Skill。
- MCP usage view 在默认展示/注入候选前过滤已隔离 server。
- 小黑屋区块仍显示已隔离项，供用户恢复使用。

## 7. 数据模型

状态文件：

```text
{runtime_dir}/skill-mcp-quarantine.json
```

示例：

```json
{
  "schema_version": 1,
  "updated_at": "2026-06-05T12:00:00+08:00",
  "entries": {
    "skill:unused-skill": {
      "entity_key": "skill:unused-skill",
      "entity_type": "skill",
      "identifier": "unused-skill",
      "display_name": "unused-skill",
      "reason": "unused_30d",
      "blocked_by": "system",
      "blocked_at": "2026-06-05T12:00:00+08:00",
      "usage_30d": 0,
      "isolation_status": "moved",
      "migration_warning_count": 0,
      "migration_warnings": [],
      "isolation_targets": [
        {
          "type": "skill",
          "status": "moved",
          "original_path": "/path/to/skill",
          "quarantine_path": "/runtime/skill-mcp-quarantine/skills/skill-unused"
        }
      ]
    }
  }
}
```

## 8. API

本地 token server 新增：

```http
POST /skill-quarantine
```

请求：

```json
{
  "action": "block|unblock|block-all|block-grace-all",
  "entity_key": "skill:unused-skill"
}
```

响应：

```json
{
  "ok": true,
  "action": "block",
  "entry": {},
  "migration_warning_count": 0,
  "refresh_started_now": true
}
```

接口仅监听本机服务，并沿用 OpenRelix 本地面板的 token/本地访问边界。

## 9. 隐私与安全

- 小黑屋状态只写本地 runtime。
- 面板展示 MCP server 名称，不展示 command args/env。
- JSON MCP 恢复 payload 仅用于本地恢复，不上传。
- JSON/TOML MCP 配置备份仅写入本地 runtime 隔离目录。
- repo skill 不自动移动，避免破坏项目 checkout。
- 恢复时遇到路径冲突不覆盖现有内容。

## 10. 验证计划

- 单元测试：Skill 移动隔离与恢复。
- 单元测试：JSON MCP 配置隔离与恢复。
- 单元测试：TOML MCP 禁用、备份与恢复。
- 单元测试：迁移失败与恢复冲突会记录迁移提示。
- 单元测试：已隔离 Skill/MCP 从默认视图过滤。
- 单元测试：本地 action 属性脱敏恢复性能修复不破坏按钮 payload。
- 手工验证：`openrelix skill-quarantine list --json` 能返回建议/隔离状态。
- 手工验证：刷新工作台后出现「小黑屋」导航和区块。
- 性能验证：真实 100MB 级 overview-data 下，面板生成不再卡在超大 HTML 占位符逐个恢复。
