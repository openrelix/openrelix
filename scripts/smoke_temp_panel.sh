#!/bin/zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

SCRIPT_DIR="${0:A:h}"
REPO_ROOT="${SCRIPT_DIR:h}"
PYTHON_BIN="${PYTHON_BIN:-}"
LANGUAGE="${AI_ASSET_LANGUAGE:-zh}"
ACTIVITY_SOURCE="${OPENRELIX_ACTIVITY_SOURCE:-${AI_ASSET_ACTIVITY_SOURCE:-auto}}"
STATE_DIR="${OPENRELIX_SMOKE_STATE_DIR:-}"
CODEX_HOME_VALUE="${OPENRELIX_SMOKE_CODEX_HOME:-}"
SOURCE_STATE_DIR="${OPENRELIX_SMOKE_SOURCE_STATE_DIR:-}"
SEED_CURRENT_STATE="${OPENRELIX_SMOKE_SEED_CURRENT_STATE:-0}"
OPEN_PANEL=1

usage() {
  cat <<'EOF'
Usage: scripts/smoke_temp_panel.sh [options]

Run a temporary OpenRelix smoke install and stop at the generated panel.

Options:
  --state-dir PATH       Use PATH as the temporary state root.
  --codex-home PATH      Use PATH as the temporary CODEX_HOME.
  --seed-current-state   Copy current OpenRelix data into the temporary state before rendering.
  --source-state-dir PATH
                         Source state root for --seed-current-state.
  --language zh|en       Runtime language. Default: zh.
  --activity-source SRC  Activity source: history | app-server | auto. Default: auto.
  --no-open             Print the panel path without opening it.
  -h, --help            Show this help.

Environment overrides:
  OPENRELIX_SMOKE_STATE_DIR
  OPENRELIX_SMOKE_CODEX_HOME
  OPENRELIX_SMOKE_SEED_CURRENT_STATE
  OPENRELIX_SMOKE_SOURCE_STATE_DIR
  AI_ASSET_LANGUAGE
  OPENRELIX_ACTIVITY_SOURCE / AI_ASSET_ACTIVITY_SOURCE
  PYTHON_BIN
EOF
}

resolve_python() {
  if [[ -n "$PYTHON_BIN" ]]; then
    return
  fi

  local candidate
  for candidate in \
    "$HOME/.pyenv/shims/python3" \
    /opt/homebrew/bin/python3.12 \
    /opt/homebrew/bin/python3.11 \
    /opt/homebrew/bin/python3.10 \
    /usr/local/bin/python3.12 \
    /usr/local/bin/python3.11 \
    /usr/local/bin/python3.10 \
    python3
  do
    if command -v "$candidate" >/dev/null 2>&1; then
      local resolved
      resolved="$(command -v "$candidate")"
      if "$resolved" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
      then
        PYTHON_BIN="$resolved"
        return
      fi
    fi
  done

  echo "missing Python 3.10+ interpreter" >&2
  exit 1
}

make_temp_dir() {
  local prefix="$1"
  mktemp -d "/private/tmp/${prefix}.XXXXXXXXXX"
}

seed_token_cache() {
  mkdir -p "$STATE_DIR/reports"
  "$PYTHON_BIN" - "$STATE_DIR" <<'PY'
import json
import sys
from datetime import datetime
from pathlib import Path

state_dir = Path(sys.argv[1])
cache_path = state_dir / "reports" / "token-usage-cache.json"
if cache_path.exists():
    raise SystemExit(0)

payload = {
    "available": False,
    "payload": {"daily": [], "totals": {}},
    "error": "skipped during temporary smoke validation",
    "fetched_at": datetime.now().astimezone().isoformat(),
    "window_days": 14,
}
cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

resolve_default_state_root() {
  "$PYTHON_BIN" - "$REPO_ROOT" <<'PY'
import os
import sys
from pathlib import Path

repo_root = Path(sys.argv[1])
sys.path.insert(0, str(repo_root / "scripts"))
os.environ.pop("AI_ASSET_STATE_DIR", None)

from asset_runtime import default_state_root  # noqa: E402

print(default_state_root())
PY
}

copy_state_subtree() {
  local relative_path="$1"
  local source_path="$SOURCE_STATE_DIR/$relative_path"
  local target_path="$STATE_DIR/$relative_path"

  if [[ ! -e "$source_path" ]]; then
    return
  fi

  if [[ -d "$source_path" ]]; then
    mkdir -p "$target_path"
    cp -R "$source_path/." "$target_path/"
  else
    mkdir -p "${target_path:h}"
    cp "$source_path" "$target_path"
  fi
}

seed_current_state() {
  case "$SEED_CURRENT_STATE" in
    1|true|TRUE|yes|YES)
      ;;
    *)
      return
      ;;
  esac

  if [[ -z "$SOURCE_STATE_DIR" ]]; then
    SOURCE_STATE_DIR="$(resolve_default_state_root)"
  fi

  if [[ ! -d "$SOURCE_STATE_DIR" ]]; then
    echo "source state root does not exist: $SOURCE_STATE_DIR" >&2
    exit 1
  fi

  if [[ "$SOURCE_STATE_DIR" == "$STATE_DIR" ]]; then
    echo "source state root must differ from temporary state root" >&2
    exit 1
  fi

  echo "[seed] 复制当前 state 数据到临时目录..."
  copy_state_subtree "raw/daily"
  copy_state_subtree "raw/windows"
  copy_state_subtree "consolidated/daily"
  copy_state_subtree "registry"
  copy_state_subtree "reviews"

  AI_ASSET_STATE_DIR="$STATE_DIR" \
  CODEX_HOME="$CODEX_HOME_VALUE" \
  AI_ASSET_LANGUAGE="$LANGUAGE" \
  OPENRELIX_ACTIVITY_SOURCE="$ACTIVITY_SOURCE" \
    "$PYTHON_BIN" "$REPO_ROOT/scripts/build_overview.py"
  echo "        完成"
}

while (( $# > 0 )); do
  case "$1" in
    --state-dir)
      STATE_DIR="${2:?missing value for --state-dir}"
      shift 2
      ;;
    --state-dir=*)
      STATE_DIR="${1#--state-dir=}"
      shift
      ;;
    --codex-home)
      CODEX_HOME_VALUE="${2:?missing value for --codex-home}"
      shift 2
      ;;
    --codex-home=*)
      CODEX_HOME_VALUE="${1#--codex-home=}"
      shift
      ;;
    --seed-current-state)
      SEED_CURRENT_STATE=1
      shift
      ;;
    --source-state-dir)
      SOURCE_STATE_DIR="${2:?missing value for --source-state-dir}"
      shift 2
      ;;
    --source-state-dir=*)
      SOURCE_STATE_DIR="${1#--source-state-dir=}"
      shift
      ;;
    --language)
      LANGUAGE="${2:?missing value for --language}"
      shift 2
      ;;
    --language=*)
      LANGUAGE="${1#--language=}"
      shift
      ;;
    --activity-source)
      ACTIVITY_SOURCE="${2:?missing value for --activity-source}"
      shift 2
      ;;
    --activity-source=*)
      ACTIVITY_SOURCE="${1#--activity-source=}"
      shift
      ;;
    --no-open)
      OPEN_PANEL=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown smoke argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

resolve_python

export PYTHON_BIN

if [[ -z "$STATE_DIR" ]]; then
  STATE_DIR="$(make_temp_dir "openrelix-smoke")"
fi
if [[ -z "$CODEX_HOME_VALUE" ]]; then
  CODEX_HOME_VALUE="$(make_temp_dir "openrelix-codex-smoke")"
fi
seed_token_cache

echo "OpenRelix temporary smoke run"
echo "repo:       $REPO_ROOT"
echo "state dir:  $STATE_DIR"
echo "codex home: $CODEX_HOME_VALUE"
echo "language:   $LANGUAGE"
if [[ "$SEED_CURRENT_STATE" == "1" || "$SEED_CURRENT_STATE" == "true" || "$SEED_CURRENT_STATE" == "TRUE" || "$SEED_CURRENT_STATE" == "yes" || "$SEED_CURRENT_STATE" == "YES" ]]; then
  echo "seed data:  current state"
fi
echo

"$REPO_ROOT/install/install.sh" \
  --minimal \
  --record-memory-only \
  --language "$LANGUAGE" \
  --activity-source "$ACTIVITY_SOURCE" \
  --state-dir "$STATE_DIR" \
  --codex-home "$CODEX_HOME_VALUE"

seed_current_state

AI_ASSET_STATE_DIR="$STATE_DIR" \
CODEX_HOME="$CODEX_HOME_VALUE" \
AI_ASSET_LANGUAGE="$LANGUAGE" \
OPENRELIX_ACTIVITY_SOURCE="$ACTIVITY_SOURCE" \
  "$PYTHON_BIN" "$REPO_ROOT/scripts/openrelix.py" doctor || {
    echo "doctor reported warnings or failures; continuing to generated panel." >&2
  }

AI_ASSET_STATE_DIR="$STATE_DIR" \
CODEX_HOME="$CODEX_HOME_VALUE" \
AI_ASSET_LANGUAGE="$LANGUAGE" \
OPENRELIX_ACTIVITY_SOURCE="$ACTIVITY_SOURCE" \
  "$PYTHON_BIN" "$REPO_ROOT/scripts/openrelix.py" core

PANEL_PATH="$STATE_DIR/reports/panel.html"
if [[ ! -f "$PANEL_PATH" ]]; then
  echo "panel was not generated: $PANEL_PATH" >&2
  exit 1
fi

echo
echo "panel: $PANEL_PATH"

if (( OPEN_PANEL )); then
  if [[ "$OSTYPE" == darwin* ]] && command -v open >/dev/null 2>&1; then
    open "$PANEL_PATH"
  else
    echo "open this file in a browser to inspect the panel:"
    echo "$PANEL_PATH"
  fi
fi
