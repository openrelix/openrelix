#!/bin/zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export AI_ASSET_REFRESH_TOKEN=1

SCRIPT_DIR="${0:A:h}"
REPO_ROOT="${SCRIPT_DIR:h}"

if [[ -z "${PYTHON_BIN:-}" ]]; then
  for candidate in \
    /opt/homebrew/bin/python3.12 \
    /opt/homebrew/bin/python3.11 \
    /opt/homebrew/bin/python3.10 \
    /usr/local/bin/python3.12 \
    /usr/local/bin/python3.11 \
    /usr/local/bin/python3.10 \
    python3
  do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$(command -v "$candidate")"
      break
    fi
  done
fi

if [[ -z "${PYTHON_BIN:-}" ]]; then
  echo "missing Python 3.10+ interpreter" >&2
  exit 1
fi

sync_codex_memory_summary_if_enabled() {
  local memory_mode=""
  local codex_home="${CODEX_HOME:-$HOME/.codex}"
  memory_mode="$(
    "$PYTHON_BIN" - "$REPO_ROOT" <<'PY'
import sys

repo_root = sys.argv[1]
sys.path.insert(0, repo_root + "/scripts")

from asset_runtime import get_memory_mode  # noqa: E402

print(get_memory_mode())
PY
  )"
  if [[ "$memory_mode" == "codex-context" ]]; then
    "$PYTHON_BIN" "$REPO_ROOT/scripts/build_codex_memory_summary.py" \
      --memory-summary "$codex_home/memories/memory_summary.md"
  fi
}

"$PYTHON_BIN" "$REPO_ROOT/scripts/collect_codex_activity.py" --date "$(date +%F)" --stage manual
sync_codex_memory_summary_if_enabled
"$PYTHON_BIN" "$REPO_ROOT/scripts/build_overview.py"
