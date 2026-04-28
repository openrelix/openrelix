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

target_date="${OPENRELIX_REFRESH_DATE:-$(date +%F)}"
stage="${OPENRELIX_REFRESH_STAGE:-manual}"
learn_memory="${OPENRELIX_REFRESH_LEARN_MEMORY:-0}"
learn_window_days="${OPENRELIX_REFRESH_LEARN_WINDOW_DAYS:-0}"
skip_unchanged="${OPENRELIX_REFRESH_SKIP_UNCHANGED:-0}"

while (( $# > 0 )); do
  case "$1" in
    --learn-memory)
      learn_memory=1
      shift
      ;;
    --date)
      target_date="${2:?missing value for --date}"
      shift 2
      ;;
    --stage)
      stage="${2:?missing value for --stage}"
      shift 2
      ;;
    --learn-window-days)
      learn_window_days="${2:?missing value for --learn-window-days}"
      shift 2
      ;;
    --learn-window-days=*)
      learn_window_days="${1#--learn-window-days=}"
      shift
      ;;
    --skip-if-unchanged)
      skip_unchanged=1
      shift
      ;;
    *)
      echo "unknown refresh_overview argument: $1" >&2
      exit 2
      ;;
  esac
done

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

case "${learn_memory:l}" in
  1|true|yes|on)
    extra_args=()
    if [[ "$learn_window_days" =~ '^[0-9]+$' ]] && (( learn_window_days > 0 )); then
      extra_args=(--learn-window-days "$learn_window_days")
    fi
    case "${skip_unchanged:l}" in
      1|true|yes|on)
        extra_args+=(--skip-if-unchanged)
        ;;
    esac
    /bin/zsh "$REPO_ROOT/scripts/nightly_pipeline.sh" "$target_date" "$stage" "${extra_args[@]}"
    exit 0
    ;;
esac

"$PYTHON_BIN" "$REPO_ROOT/scripts/collect_codex_activity.py" --date "$target_date" --stage manual
sync_codex_memory_summary_if_enabled
"$PYTHON_BIN" "$REPO_ROOT/scripts/build_overview.py"
