# OpenRelix 文档索引

> 语言版本：[English](README.md) | 简体中文

这个目录采用双语文档规则：

- Markdown 是 agent/贡献者阅读的主源，适合架构、隐私、验证、发布和协作材料。
- 每份 Markdown 文档都应该同时有英文版和中文版。
- 为避免破坏已有链接，现有默认语言文件名保持稳定。英文默认文档的中文 companion 使用 `.zh-CN.md`；中文默认文档的英文 companion 使用 `.en.md`。
- `local-docs/` 和 `docs/developer/` 下的 HTML 由这些 Markdown 生成；源头仍是 Markdown。

## Agent 友好的 Markdown

| 主题 | 英文 | 中文 |
| --- | --- | --- |
| 系统概览 | [system-overview.md](system-overview.md) | [system-overview.zh-CN.md](system-overview.zh-CN.md) |
| 技术方案 | [technical-solution.en.md](technical-solution.en.md) | [technical-solution.md](technical-solution.md) |
| 开发者详细指南 | [developer-guide.en.md](developer-guide.en.md) | [developer-guide.md](developer-guide.md) |
| 数据契约 | [data-contracts.md](data-contracts.md) | [data-contracts.zh-CN.md](data-contracts.zh-CN.md) |
| 验证矩阵 | [validation-matrix.md](validation-matrix.md) | [validation-matrix.zh-CN.md](validation-matrix.zh-CN.md) |
| 隐私与分发边界 | [privacy-and-distribution.md](privacy-and-distribution.md) | [privacy-and-distribution.zh-CN.md](privacy-and-distribution.zh-CN.md) |
| 隐私威胁模型 | [privacy-threat-model.md](privacy-threat-model.md) | [privacy-threat-model.zh-CN.md](privacy-threat-model.zh-CN.md) |
| 发布检查清单 | [release-checklist.md](release-checklist.md) | [release-checklist.zh-CN.md](release-checklist.zh-CN.md) |
| 指标字典 | [metric-dictionary.md](metric-dictionary.md) | [metric-dictionary.zh-CN.md](metric-dictionary.zh-CN.md) |
| 开源安装与项目说明 | [open-source-install-and-project-overview.en.md](open-source-install-and-project-overview.en.md) | [open-source-install-and-project-overview.md](open-source-install-and-project-overview.md) |

## 富交互 HTML 页面

这些页面故意保留为 HTML，因为它们是视觉或交互页面：

- [index.html](index.html)：产品首页。
- [product-showcase.html](product-showcase.html)：产品展示和截图。
- [getting-started.html](getting-started.html)：带语言截图的面板上手页。
- [changelog/v0.x.html](changelog/v0.x.html)：双语预览版更新日志。
- [developer/developer-guide.html](developer/developer-guide.html)：开发者图解指南，可随站点发布。

它们应该在单页内保留中英语言切换，而不是拆成 Markdown 双文件。

## 维护规则

新增 `docs/*.md` 时，同一改动里补语言 companion。修改 Markdown 后运行 `python3 scripts/build_local_docs.py` 刷新 `local-docs/` 和 `docs/developer/`；需要持续同步时运行 `python3 scripts/build_local_docs.py --watch`。新增或修改 `docs/*.html` 时，沿用现有语言切换模式，保持页面自身双语。
