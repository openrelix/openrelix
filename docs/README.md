# OpenRelix Docs Index

> Languages: English | [简体中文](README.zh-CN.md)

This directory uses a bilingual documentation rule:

- Markdown documents are the canonical agent-readable source for contributor, architecture, privacy, validation, and release material.
- Each Markdown document should have an English and a Chinese version.
- Existing default-language filenames stay stable to avoid breaking links. A Chinese companion uses `.zh-CN.md`; an English companion for a Chinese-default document uses `.en.md`.
- HTML under `local-docs/` and `docs/developer/` is generated from these Markdown files. Markdown remains the source.

## Agent-Readable Markdown

| Topic | English | Chinese |
| --- | --- | --- |
| System overview | [system-overview.md](system-overview.md) | [system-overview.zh-CN.md](system-overview.zh-CN.md) |
| Technical solution | [technical-solution.en.md](technical-solution.en.md) | [technical-solution.md](technical-solution.md) |
| Detailed developer guide | [developer-guide.en.md](developer-guide.en.md) | [developer-guide.md](developer-guide.md) |
| Data contracts | [data-contracts.md](data-contracts.md) | [data-contracts.zh-CN.md](data-contracts.zh-CN.md) |
| Validation matrix | [validation-matrix.md](validation-matrix.md) | [validation-matrix.zh-CN.md](validation-matrix.zh-CN.md) |
| Privacy boundary | [privacy-and-distribution.md](privacy-and-distribution.md) | [privacy-and-distribution.zh-CN.md](privacy-and-distribution.zh-CN.md) |
| Privacy threat model | [privacy-threat-model.md](privacy-threat-model.md) | [privacy-threat-model.zh-CN.md](privacy-threat-model.zh-CN.md) |
| Release checklist | [release-checklist.md](release-checklist.md) | [release-checklist.zh-CN.md](release-checklist.zh-CN.md) |
| Metric dictionary | [metric-dictionary.md](metric-dictionary.md) | [metric-dictionary.zh-CN.md](metric-dictionary.zh-CN.md) |
| Open-source install overview | [open-source-install-and-project-overview.en.md](open-source-install-and-project-overview.en.md) | [open-source-install-and-project-overview.md](open-source-install-and-project-overview.md) |

## Rich HTML Pages

These pages intentionally remain HTML because they are visual or interactive:

- [index.html](index.html): product homepage.
- [product-showcase.html](product-showcase.html): product showcase and screenshots.
- [getting-started.html](getting-started.html): panel walkthrough with language-specific screenshots.
- [changelog/v0.x.html](changelog/v0.x.html): bilingual preview changelog.
- [developer/developer-guide.html](developer/developer-guide.html): visual developer guide, ready for static-site publishing.

They should keep Chinese and English language controls in the page itself rather than being split into separate Markdown files.

## Maintenance Rule

When adding a new `docs/*.md` file, add its language companion in the same change. After editing Markdown, run `python3 scripts/build_local_docs.py` to refresh both `local-docs/` and `docs/developer/`; use `python3 scripts/build_local_docs.py --watch` while writing. When adding or editing a `docs/*.html` page, keep the page bilingual through the existing language-switching pattern.
