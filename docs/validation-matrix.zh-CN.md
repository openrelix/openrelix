# OpenRelix 验证矩阵

> 语言版本：[English](validation-matrix.md) | 简体中文

用这张矩阵为一次改动选择最小但有用的验证集合。先跑 common checks，再按你改到的文件补对应行。

## 通用检查

每次 OpenRelix 改动都要跑：

```bash
python3 scripts/check_personal_info.py
git diff --check
```

如果 worktree 里有无关本地文件，不要 stage 或修改它们。交付时说明相关 dirty state。

## 按改动类型选择检查

| 改动类型 | 常见文件 | 额外检查 |
| --- | --- | --- |
| Docs only | `README*.md`, `docs/*.md`, `.github/*` | bilingual companion check，link review，version review against `package.json`，privacy check |
| Public site docs | `docs/*.html`, `docs/assets/*` | local HTTP preview，布局变化时做 browser screenshot/interaction check，privacy check on screenshots/assets |
| Python scripts | `scripts/*.py`, `scripts/openrelix_overview/*.py` | `python3 -m py_compile scripts/*.py install/*.py`，focused unit tests |
| Installer | `install/`, `ops/launchd/`, `install/templates/` | `zsh -n install/install.sh scripts/*.sh`，focused installer tests，temporary state smoke |
| Runtime path/config | `scripts/asset_runtime.py`, installer config writes | temporary `AI_ASSET_STATE_DIR` loop，config read/write focused tests |
| Host collection | `scripts/collect_codex_activity.py` | `python3 -m unittest tests/test_collect_codex_activity.py` |
| Memory policy/context | `scripts/build_codex_memory_summary.py`, `scripts/sync_host_memory_summary.py`, `scripts/openrelix_overview/memory_context.py` | `python3 -m unittest tests/test_memory_context.py tests/test_memory_summary_builder.py` |
| Curated memory pack | `scripts/build_curated_memory_pack.py`, `scripts/openrelix_overview/curated_memory.py` | `python3 -m unittest tests/test_curated_memory.py` |
| Index | `scripts/openrelix_index.py` | `python3 -m unittest tests/test_openrelix_index.py` |
| Overview/panel data | `scripts/build_overview.py`, `scripts/openrelix_overview/*`, report contract | temporary overview build，加 `PYTHONPATH=scripts python3 -m openrelix_overview.contract --state-dir "$STATE_DIR"` |
| Package surface | `package.json`, package allowlist, public plugin bundle | `npm pack --dry-run --json`，并检查文件列表 |
| Release/publish | version、changelog、GitHub release、npm publish | full test suite，package dry run，release checklist |

## 临时 State Loop

改动可能影响 state-root 行为时使用：

```bash
STATE_DIR="$(mktemp -d /tmp/openrelix-validation.XXXXXX)"
AI_ASSET_STATE_DIR="$STATE_DIR" python3 scripts/build_overview.py
python3 -m json.tool "$STATE_DIR/reports/overview-data.json" >/dev/null
PYTHONPATH=scripts python3 -m openrelix_overview.contract --state-dir "$STATE_DIR"
```

installer-to-panel smoke：

```bash
scripts/smoke_temp_panel.sh --no-open
scripts/cleanup_smoke_temp.sh --dry-run
```

只有任务明确需要真实本机数据时才 seed current state。不要把 seeded output 复制进 repo 文件。

## 发布前完整检查

release、publish、installer、docs/site 或 package-surface 改动前运行：

```bash
python3 scripts/check_personal_info.py
git diff --check
python3 -m py_compile scripts/*.py install/*.py
python3 -m unittest discover -s tests
npm pack --dry-run --json
```

检查 package list，确认不会带进 state roots、raw history、generated reports、logs、private screenshots 或 development-only harness skills。

## 交付格式

最终 review note 或 PR 里包含：

```text
Change:
Files:
Verification:
Skipped checks:
Privacy/package notes:
Residual risk:
```

命令输出摘要保持短，不粘贴私有原始日志。
