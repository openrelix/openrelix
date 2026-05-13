# 商标申请资料包

> 语言版本：[English](trademark-filing-kit.md) | 简体中文

这是一份用于为开源 OpenRelix 项目快速准备商标申请的操作清单。它不是法律意见。

最后检查日期：2026-04-27。

## 最快可行路径

先申请文字商标，不要等待 logo。

推荐第一份申请：

- 司法辖区：美国，如果项目通过 GitHub、npm 或其他触达美国用户的渠道分发。
- 商标：`OPENRELIX`
- 图样：standard characters。
- 权利人：控制项目品牌的个人或法律实体。
- 主要类别：国际分类第 9 类。
- 申请基础：如果软件已经以该商标公开可下载或可安装，用 Section 1(a) use in commerce；否则用 Section 1(b) intent to use。

只有在同一商标下提供 hosted web service、SaaS product 或 online non-downloadable software service 时，才添加第 42 类。当前预览 repo 是 downloadable CLI / installer project，因此第 9 类是清晰的首选。

优先保护 `OPENRELIX`。除非商标律师确认，不要把缩写或昵称作为第一申请标志。

中国保护需要在中国单独申请 `OPENRELIX`。美国申请不会自动覆盖中国。见 [中国商标申请资料包](china-chinese-trademark-filing-kit.zh-CN.md)。

## 商品和服务草案

USPTO 申请时，优先使用 Trademark Center 中的 Trademark ID Manual。选择可接受的 ID Manual 项通常更快，也可以避免 free-form identification 的额外费用。

第 9 类草案：

```text
Downloadable computer software for creating, organizing, storing, reviewing, and
displaying reusable workflow assets, namely skills, templates, automations, and
task reviews, for use with command-line artificial intelligence developer tools
```

如果 Trademark Center 提供 fill-in ID Manual entry，保留所选 entry 的结构，只填 function 和 field 中的项目特定文字。如果确实放不进 accepted ID Manual entry，预期官方申请费会更高。

仅在有 hosted service 时使用第 42 类草案：

```text
Providing temporary use of online non-downloadable software for creating,
organizing, storing, reviewing, and displaying reusable workflow assets, namely
skills, templates, automations, and task reviews, for use with command-line
artificial intelligence developer tools
```

## 使用证据清单

如果按 Section 1(a) use-in-commerce 申请，需要截取真实公开页面，显示商标与可下载软件一起出现。

最佳证据：

- npm package 页面，显示 `openrelix`、项目描述和安装命令。
- GitHub README 页面，显示 `OpenRelix` 和 `npx openrelix install`。
- GitHub release 或 download 页面，显示 `OpenRelix` 和可下载 artifact 或 ZIP。

截图或网页打印件应包含：

- 与申请一致的商标，最好是 `OpenRelix`。
- download、install、package、release 或 repository action，证明软件可获得。
- URL 和访问日期。

不要把申请人地址、付款方式、账号信息或律师沟通记录存入本仓库。

## 权利人资料

申请人或代理应直接在官方表单或律师 intake 中输入：

- 申请人法律姓名。
- 申请人类型：individual 或 legal entity。
- 若为个人，提供 citizenship。
- 若为公司，提供 entity jurisdiction。
- domicile address。
- correspondence email 和 phone。
- signature name 和 title。
- payment method。

这些都是私密资料，不进入 repo、docs、fixtures 或 release artifacts。

## 申请前检查

- `OPENRELIX` 是否是第一保护目标。
- 是否确定第 9 类是当前主申请类别。
- 是否真的已经公开可下载；如果不确定，优先用 intent-to-use。
- 是否已有可用 specimen。
- 是否没有把申请人私密资料写进公开仓库。
- 是否和中国申请计划一致。
