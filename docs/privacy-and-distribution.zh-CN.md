# 隐私与分发边界

> 语言版本：[English](privacy-and-distribution.md) | 简体中文

这个仓库应该可以公开分享，但不能携带某个人本机的运行时状态。

贡献者和 connector 风险审查请看 [隐私威胁模型](privacy-threat-model.zh-CN.md)。

## 应该共享什么

- repo 源码本身。
- 安装脚本。
- `.agents/skills/` 下的 canonical skills。
- 模板和 schema。
- 为文档专门制作的脱敏截图或示例输出。

## 应该留在本地什么

- 原始 Codex history。
- runtime caches 和 logs。
- 绑定某个用户工作的生成报告。
- 真实 registry entries 和 task reviews。
- 任何包含 secrets、credentials、内部路径或专有任务上下文的内容。

## 推荐的公开叙事

1. 仓库提供可复用自动化和 installer。
2. 用户数据保存在 installer 管理的外部 state root。
3. repo-local skills 在本仓库内自动可用。
4. 可选 global install 会让这些 skills 在任意仓库可用。
5. 后台服务和夜间整理是安装器开启的可选能力。
