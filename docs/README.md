# OpenRelix Docs Index

> Languages: English | [简体中文](README.zh-CN.md)

This directory uses a bilingual documentation rule:

- Markdown documents are the canonical agent-readable source for contributor, architecture, privacy, validation, and release material.
- Each Markdown document should have an English and a Chinese version.
- Existing default-language filenames stay stable to avoid breaking links. A Chinese companion uses `.zh-CN.md`; an English companion for a Chinese-default document uses `.en.md`.
- HTML pages are reserved for rich public experiences that need layout, screenshots, theme switching, or interactive language switching.

## Agent-Readable Markdown

| Topic | English | Chinese |
| --- | --- | --- |
| System overview | [system-overview.md](system-overview.md) | [system-overview.zh-CN.md](system-overview.zh-CN.md) |
| Technical solution | [technical-solution.en.md](technical-solution.en.md) | [technical-solution.md](technical-solution.md) |
| Developer guide | [developer-guide.en.md](developer-guide.en.md) | [developer-guide.md](developer-guide.md) |
| Contributor onboarding | [contributor-onboarding.md](contributor-onboarding.md) | [contributor-onboarding.zh-CN.md](contributor-onboarding.zh-CN.md) |
| Data contracts | [data-contracts.md](data-contracts.md) | [data-contracts.zh-CN.md](data-contracts.zh-CN.md) |
| Validation matrix | [validation-matrix.md](validation-matrix.md) | [validation-matrix.zh-CN.md](validation-matrix.zh-CN.md) |
| Privacy boundary | [privacy-and-distribution.md](privacy-and-distribution.md) | [privacy-and-distribution.zh-CN.md](privacy-and-distribution.zh-CN.md) |
| Privacy threat model | [privacy-threat-model.md](privacy-threat-model.md) | [privacy-threat-model.zh-CN.md](privacy-threat-model.zh-CN.md) |
| Release checklist | [release-checklist.md](release-checklist.md) | [release-checklist.zh-CN.md](release-checklist.zh-CN.md) |
| Learning guide | [learning-guide.en.md](learning-guide.en.md) | [learning-guide.md](learning-guide.md) |
| Metric dictionary | [metric-dictionary.md](metric-dictionary.md) | [metric-dictionary.zh-CN.md](metric-dictionary.zh-CN.md) |
| Open-source install overview | [open-source-install-and-project-overview.en.md](open-source-install-and-project-overview.en.md) | [open-source-install-and-project-overview.md](open-source-install-and-project-overview.md) |
| build_overview isolation plan | [build-overview-isolation-plan.en.md](build-overview-isolation-plan.en.md) | [build-overview-isolation-plan.md](build-overview-isolation-plan.md) |
| Trademark filing kit | [trademark-filing-kit.md](trademark-filing-kit.md) | [trademark-filing-kit.zh-CN.md](trademark-filing-kit.zh-CN.md) |
| China trademark filing kit | [china-chinese-trademark-filing-kit.md](china-chinese-trademark-filing-kit.md) | [china-chinese-trademark-filing-kit.zh-CN.md](china-chinese-trademark-filing-kit.zh-CN.md) |
| Dual trademark action sheet | [trademark-dual-filing-action-sheet.md](trademark-dual-filing-action-sheet.md) | [trademark-dual-filing-action-sheet.zh-CN.md](trademark-dual-filing-action-sheet.zh-CN.md) |

## Rich HTML Pages

These pages intentionally remain HTML because they are visual or interactive:

- [index.html](index.html): product homepage.
- [product-showcase.html](product-showcase.html): product showcase and screenshots.
- [getting-started.html](getting-started.html): panel walkthrough with language-specific screenshots.
- [changelog/v0.x.html](changelog/v0.x.html): bilingual preview changelog.

They should keep Chinese and English language controls in the page itself rather than being split into separate Markdown files.

## Maintenance Rule

When adding a new `docs/*.md` file, add its language companion in the same change. When adding or editing a `docs/*.html` page, keep the page bilingual through the existing language-switching pattern.
