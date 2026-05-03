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
  if [[ "$memory_mode" == "integrated" ]]; then
    "$PYTHON_BIN" "$REPO_ROOT/scripts/build_codex_memory_summary.py" \
      --memory-summary "$codex_home/memories/memory_summary.md"
  fi
}

build_codex_native_display_cache_if_enabled() {
  local display_polish="${OPENRELIX_ENABLE_NATIVE_DISPLAY_POLISH:-auto}"
  case "${display_polish:l}" in
    0|false|no|off|disabled)
      return 0
      ;;
    1|true|yes|on|enabled)
      ;;
    auto|"")
      local runtime_language=""
      runtime_language="$(
        "$PYTHON_BIN" - "$REPO_ROOT" <<'PY'
import sys

repo_root = sys.argv[1]
sys.path.insert(0, repo_root + "/scripts")

from asset_runtime import get_runtime_language  # noqa: E402

print(get_runtime_language())
PY
      )" || runtime_language=""
      if [[ "$runtime_language" != "zh" ]]; then
        return 0
      fi
      ;;
    *)
      return 0
      ;;
  esac

  local memory_mode=""
  memory_mode="$(
    "$PYTHON_BIN" - "$REPO_ROOT" <<'PY'
import sys

repo_root = sys.argv[1]
sys.path.insert(0, repo_root + "/scripts")

from asset_runtime import get_memory_mode  # noqa: E402

print(get_memory_mode())
PY
  )"
  if [[ "$memory_mode" != "integrated" ]]; then
    return 0
  fi
  if ! "$PYTHON_BIN" "$REPO_ROOT/scripts/build_codex_native_display_cache.py"; then
    echo "nightly_pipeline: codex native display polish failed; using source-text fallback." >&2
  fi
}

rebuild_sqlite_index_if_available() {
  if [[ "${OPENRELIX_DISABLE_SQLITE_INDEX_REBUILD:-0}" == "1" ]]; then
    return 0
  fi
  if [[ ! -f "$REPO_ROOT/scripts/openrelix_index.py" ]]; then
    return 0
  fi
  if ! "$PYTHON_BIN" "$REPO_ROOT/scripts/openrelix_index.py" rebuild >/dev/null; then
    echo "nightly_pipeline: sqlite index rebuild failed; JSONL/raw outputs remain authoritative." >&2
  fi
}

exit_if_latest_model_run_failed() {
  local failure_message=""
  set +e
  failure_message="$(
    "$PYTHON_BIN" - "$REPO_ROOT" "$target_date" <<'PY'
import json
import sys

repo_root = sys.argv[1]
target_date = sys.argv[2]
sys.path.insert(0, repo_root + "/scripts")

from asset_runtime import get_runtime_paths  # noqa: E402

summary_path = get_runtime_paths().consolidated_daily_dir / target_date / "summary.json"
try:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(0)

decision = summary.get("selection_decision") or {}
failed = (
    summary.get("last_run_model_status") == "failed"
    or summary.get("model_status") == "failed"
    or decision.get("candidate_model_status") == "failed"
)
if not failed:
    raise SystemExit(0)

hint = (
    summary.get("last_run_model_error_hint")
    or summary.get("model_error_hint")
    or decision.get("candidate_model_error_hint")
    or "model summarization failed"
)
print(hint)
raise SystemExit(1)
PY
  )"
  local probe_status=$?
  set -e
  if (( probe_status != 0 )); then
    echo "nightly_pipeline: model summarization failed; generated fallback summary." >&2
    if [[ -n "$failure_message" ]]; then
      echo "$failure_message" >&2
    fi
    exit "$probe_status"
  fi
}

target_date="${1:-$(date +%F)}"
stage="${2:-manual}"
extra_args=("${@:3}")
nightly_args=()
learn_window_days=0
defer_global_refresh=0
skip_learning_collect=0

for ((i = 1; i <= ${#extra_args[@]}; i++)); do
  arg="${extra_args[$i]}"
  case "$arg" in
    --learn-window-days)
      if (( i < ${#extra_args[@]} )); then
        learn_window_days="${extra_args[$((i + 1))]}"
      fi
      ;;
    --learn-window-days=*)
      learn_window_days="${arg#--learn-window-days=}"
      ;;
    --defer-global-refresh)
      defer_global_refresh=1
      continue
      ;;
    --skip-learning-collect)
      skip_learning_collect=1
      continue
      ;;
  esac
  nightly_args+=("$arg")
done

"$PYTHON_BIN" "$REPO_ROOT/scripts/collect_codex_activity.py" --date "$target_date" --stage "$stage"
if [[ "$skip_learning_collect" != "1" ]] && [[ "$learn_window_days" =~ '^[0-9]+$' ]] && (( learn_window_days > 0 )); then
  "$PYTHON_BIN" - "$target_date" "$learn_window_days" <<'PY' | while IFS= read -r learning_date; do
from datetime import date, timedelta
import sys

target = date.fromisoformat(sys.argv[1])
days = int(sys.argv[2])
for offset in range(1, days + 1):
    print((target - timedelta(days=offset)).isoformat())
PY
    "$PYTHON_BIN" "$REPO_ROOT/scripts/collect_codex_activity.py" --date "$learning_date" --stage final
  done
fi
"$PYTHON_BIN" "$REPO_ROOT/scripts/nightly_consolidate.py" --date "$target_date" --stage "$stage" "${nightly_args[@]}"
if [[ "$defer_global_refresh" != "1" ]]; then
  rebuild_sqlite_index_if_available
  sync_codex_memory_summary_if_enabled
  build_codex_native_display_cache_if_enabled
  "$PYTHON_BIN" "$REPO_ROOT/scripts/build_overview.py"
fi
exit_if_latest_model_run_failed
