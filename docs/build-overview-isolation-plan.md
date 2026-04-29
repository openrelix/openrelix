# build_overview 隔离重构方案

状态：设计方案，尚未实施。

本方案针对 `scripts/build_overview.py` 的职责膨胀问题，目标是在不破坏现有命令、测试和生成结果的前提下，把 overview 构建器逐步拆成清晰的内部模块。方案已经过独立 Codex subreview 审阅，最终结果为 10/10 PASS，Must Fix 和 Should Fix 均已清空。

## 背景

`scripts/build_overview.py` 当前同时承担这些职责：

- 运行路径和语言配置初始化
- JSONL、reviews、raw capture、nightly summary、Codex native memory 读取
- 脱敏、品牌文本归一化、本地路径 linkify
- i18n 和展示文案转换
- 资产、复用记录、价值分和 summary terms 统计
- window overview、project context 和 topic inference
- OpenRelix managed memory 聚合
- Codex native memory 解析、对照和高亮
- token usage 拉取和面板数据构建
- Markdown、CSV、HTML、CSS、JS 输出
- 最终 `reports/*` 写入

最大热点是 `build_html()`、`build_data()`、`build_markdown()`、Codex native memory parser、memory registry builder 和多组 panel renderer。另一个关键问题是 `scripts/token_live_server.py` 直接 import 整个 `build_overview`，只是为了复用 token 和 update-token helper，导致 overview 生成器变成隐藏服务依赖。

## 目标

1. 保留 `scripts/build_overview.py` 作为兼容入口，避免一次性破坏现有调用方。
2. 将真实实现迁到 `scripts/openrelix_overview/` 内部包。
3. 让 `overview-data.json` 成为 data builder 与 renderer 之间的稳定契约。
4. 保持 OpenRelix managed memory 和 Codex native memory 两层产品边界，不为了代码复用合并它们。
5. 先隔离副作用和核心边界，再迁移大模板；避免一开始就重写 `build_html()`。
6. 保持 runtime state 在 repo 外部，repo 只保存可复用代码、模板、测试和文档。

## 非目标

- 不在第一阶段重写 panel UI。
- 不改变生成文件路径：仍然输出 `overview-data.json`、`overview.md`、`overview.csv`、`panel.html`。
- 不把 runtime state、raw history、生成报告、私有路径或用户内容写入 repo。
- 不删除 `build_overview` 现有函数名，直到调用方和测试迁移完成。

## 目标模块结构

```text
scripts/openrelix_overview/
  __init__.py
  api.py                 # 新代码使用的稳定 public imports
  entrypoint.py          # main() 编排和 report 写入
  context.py             # BuildContext: paths, language, memory mode, current time
  io.py                  # jsonl/review/nightly/raw/codex-memory readers
  schema.py              # OverviewData / section view model contracts
  contract.py            # schema version, JSON Schema, normalized golden comparison
  config.py              # live token host/port/endpoint 等常量
  redaction.py           # denylist, brand normalization, path redaction
  i18n.py                # localized labels 和文案 helpers
  assets.py              # assets, usage events, review enrichment, value scoring
  windows.py             # window overview, project context, topic inference
  memory_registry.py     # OpenRelix managed memory grouping 和 usage matching
  codex_native.py        # Codex native memory parsing, comparison, highlight
  token_usage.py         # pure token payload shaping 和 token card data
  token_fetcher.py       # impure ccusage subprocess adapter，注入 env/clock/runner
  update_secret.py       # read_or_create_update_token；runtime secret IO，无 import-time writes
  summary_terms.py       # term extraction/ranking
  builders.py            # build_overview_data orchestration
  renderers/
    markdown.py
    csv.py
    panel.py
    panel_templates.py
```

## 兼容入口策略

`scripts/build_overview.py` 不能直接变成简单的 `import *` facade。现有测试和调用方会 monkeypatch 这些模块级变量或函数：

- `PATHS`
- `REGISTRY_DIR`
- `REPORTS_DIR`
- `REVIEWS_DIR`
- `CONSOLIDATED_DIR`
- `RAW_DAILY_DIR`
- `resolve_ccusage_daily`
- `load_primary_and_active_nightly_summaries`

如果把函数直接搬到新模块，这些函数会读取新模块自己的 globals，`build_overview.X = ...` 的 monkeypatch 将不再生效。

迁移期采用两种机制：

- 纯函数：没有 runtime globals 依赖的 helper 可以从 `openrelix_overview.api` 直接 re-export。
- 依赖可变全局的函数：继续在 `scripts/build_overview.py` 保留 wrapper，wrapper 在调用时从 facade globals 构造 `BuildContext`，并把依赖显式传入新实现。

示例：

```python
#!/usr/bin/env python3
from openrelix_overview.api import *  # pure helpers only
from openrelix_overview.context import BuildContext
from openrelix_overview import entrypoint as _entrypoint


def build_data(assets, usage_events, reviews, language=None):
    context = BuildContext.from_facade_globals(globals())
    return builders.build_data(
        assets,
        usage_events,
        reviews,
        context=context,
        resolve_ccusage_daily_func=globals()["resolve_ccusage_daily"],
    )


def main():
    return _entrypoint.main(BuildContext.from_facade_globals(globals()))


if __name__ == "__main__":
    main()
```

只有当测试和 runtime caller 都不再 monkeypatch facade globals 后，才删除这些 wrapper。

## 数据契约

`overview-data.json` 是 renderer 的稳定输入，不允许 renderer 再读取 JSONL、raw capture、Codex memory 文件或 runtime state。

契约规则：

- 顶层增加 `schema_version`。
- section 数据保持独立 namespace，例如 `token_usage`、`memory_registry`、`codex_native_memory`、`window_overview`、`project_context_views`、`summary_term_views`。
- 在 section namespace 内新增字段允许兼容添加。
- 字段删除、字段类型变化、enum 变化、anchor/id 变化必须 bump schema version，并写迁移说明。
- 每个迁移阶段都要跑 schema 校验和 normalized golden fixture diff。

## 副作用边界

原则：普通 import 不应该创建 runtime 文件、不应该写 repo、不应该触发 subprocess。

- `entrypoint.py` 和 `io.py` 负责读写文件。
- `builders.py` 和各 section builder 只接受输入并返回 plain data。
- `token_usage.py` 只做纯数据转换。
- `token_fetcher.py` 才允许调用 `npx` / subprocess，但必须支持注入 env、clock、runner 方便测试。
- `update_secret.py` 允许 read/create update token secret，但不能在 import 时读写文件。

## Phase 0：安全网和契约基线

先补验证，不做大拆分。

每批迁移前跑基础检查：

```bash
STATE_DIR="$(mktemp -d /tmp/openrelix-overview-baseline.XXXXXX)"
python3 -m py_compile scripts/build_overview.py tests/test_nightly_logic.py
python3 -m unittest tests/test_nightly_logic.py tests/test_memory_summary_builder.py
AI_ASSET_STATE_DIR="$STATE_DIR" python3 scripts/build_overview.py
python3 -m json.tool "$STATE_DIR/reports/overview-data.json" >/dev/null
```

增加 contract 校验模块：

```bash
STATE_DIR="$(mktemp -d /tmp/openrelix-overview-contract.XXXXXX)"
AI_ASSET_STATE_DIR="$STATE_DIR" python3 scripts/build_overview.py
python3 -m json.tool "$STATE_DIR/reports/overview-data.json" >/dev/null
PYTHONPATH=scripts python3 -m openrelix_overview.contract --state-dir "$STATE_DIR"
```

`openrelix_overview.contract` 是 Phase 0 必交付：

- 校验 `overview-data.json` 的 `schema_version`
- 校验 JSON Schema
- 对 normalized `overview-data.json` 做 golden diff
- 对 `overview.md`、`overview.csv`、`panel.html` 做代表性 marker/golden 检查
- 覆盖 panel 关键 anchors、memory sections、token live scripts、window overview controls

增加 import side-effect gate：

```bash
STATE_DIR="$(mktemp -d /tmp/openrelix-import-side-effects.XXXXXX)"
PYTHONPATH=scripts AI_ASSET_STATE_DIR="$STATE_DIR" python3 - "$STATE_DIR" <<'PY'
import importlib
import pathlib
import pkgutil
import subprocess
import sys

repo_root = pathlib.Path.cwd()
state_dir = pathlib.Path(sys.argv[1])
generated_paths = [
    state_dir,
    repo_root / "reports",
    repo_root / "runtime",
    repo_root / "registry",
    repo_root / "raw",
    repo_root / "consolidated",
]

def snapshot(paths):
    rows = []
    for root in paths:
        if root.exists():
            rows.extend((root, path.relative_to(root)) for path in root.rglob("*"))
    return sorted((str(root), str(rel)) for root, rel in rows)

def git_status():
    return subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

before_files = snapshot(generated_paths)
before_status = git_status()
import build_overview
import token_live_server
import openrelix_overview
for module_info in pkgutil.walk_packages(openrelix_overview.__path__, openrelix_overview.__name__ + "."):
    importlib.import_module(module_info.name)
after_files = snapshot(generated_paths)
after_status = git_status()
assert before_files == after_files, "plain imports must not create runtime/generated files"
assert before_status == after_status, "plain imports must not change the repo working tree"
PY
```

## Phase 1：拆 token、update secret 和基础纯工具

优先解除 `token_live_server.py -> build_overview` 的耦合。

先抽取：

- `config.py`
- `token_usage.py`
- `token_fetcher.py`
- `update_secret.py`
- `redaction.py`
- `i18n.py`

`scripts/token_live_server.py` 后续应改为：

- 从 `token_usage.py` import `build_token_usage_view()`
- 从 `token_fetcher.py` import `fetch_ccusage_daily()`
- 从 `config.py` import live token constants
- 从 `update_secret.py` import `read_or_create_update_token()`

同时 `build_overview.py` 继续 wrapper 或 re-export 旧函数名，避免破坏测试。

## Phase 2：拆强测试覆盖的 domain 模块

抽取：

- `codex_native.py`
- `memory_registry.py`
- `windows.py`

这些区域已有较多测试覆盖，适合做 move-only 迁移。原则是先不改行为，只改变模块位置和依赖注入方式。

注意保持两条 memory 边界：

- OpenRelix managed memory：来自本地 registry 和 nightly consolidation。
- Codex native memory：来自 host native memory summary / index。

两者可以共享文本和路径工具，但 parser、comparison、display contract 不合并。

## Phase 3：拆 asset、summary terms 和 builders

抽取：

- `assets.py`
- `summary_terms.py`
- `builders.py`

目标是让 `build_data()` 变成 section 编排层，而不是继续包含所有业务细节。

理想形态：

```python
def build_overview_data(inputs, context):
    token = build_token_section(inputs, context)
    windows = build_window_section(inputs, context)
    managed_memory = build_managed_memory_section(inputs, context)
    native_memory = build_codex_native_section(inputs, context)
    assets = build_asset_section(inputs, context)
    return OverviewData(...)
```

## Phase 4：迁出 renderer

抽取：

- `renderers/markdown.py`
- `renderers/csv.py`
- `renderers/panel.py`

第一步可以保留大块 inline template。此阶段的目标不是让模板优雅，而是让 data layer 和 rendering layer 分离。

同时处理 source-inspection 测试：

- 如果测试必须看源码，改为读取 `renderers/panel.py`。
- 更推荐改成断言生成后的 `panel.html` marker、anchors 和脚本存在。

## Phase 5：模板资源化

在 Phase 4 稳定后再做：

- CSS 独立成 source resource
- JS 独立成 source resource
- build 时内联回单文件 `panel.html`
- 增加测试保证必要 anchors、controls、scripts、theme/language switch 存在

这一步可以延后，不应该和前面的 domain 拆分混在一个大 diff 里。

## 迁移前清单

正式实施前先列四张表：

- 可以直接 re-export 的纯函数
- 必须保留 facade wrapper 的函数
- 当前 monkeypatch `build_overview` globals 的测试和调用方
- 当前读取 `scripts/build_overview.py` 源码做断言的测试

## 验收标准

- `scripts/build_overview.py` 成为小型兼容入口，长期目标低于 200 行。
- `scripts/token_live_server.py` 不再 import 整个 `build_overview`。
- 普通 import `build_overview`、`token_live_server`、`openrelix_overview.*` 不创建 runtime/generated 文件，不改变 git status。
- `build_data()` 不再直接读取全局路径或 runtime state，而是使用 `BuildContext` 和显式 inputs。
- renderers 只消费 `OverviewData`，不读取 raw files 或 runtime state。
- `overview-data.json` 有 `schema_version`、JSON Schema 和 normalized golden-output 检查。
- `overview.md`、`overview.csv`、`panel.html` 有代表性 marker/golden 检查。
- OpenRelix managed memory 和 Codex native memory 在 data 和 UI 中仍然分层展示。
- 不引入用户路径、私有内容、raw logs、runtime state 或生成报告到 repo source。

## 推荐实施节奏

最小有效隔离版：

- Phase 0
- Phase 1 中的 `token_usage.py`、`token_fetcher.py`、`config.py`、`update_secret.py`
- facade wrapper 机制

预计 1.5-2 天。

主结构拆开但不追求模板完美：

- Phase 0-4

预计 4-6 天。

完整最终形态：

- Phase 0-5

预计 6-9 天。

