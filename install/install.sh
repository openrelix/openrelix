#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
REPO_ROOT="${SCRIPT_DIR:h}"
PYTHON_BIN="${PYTHON_BIN:-}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
CODEX_BIN="${CODEX_BIN:-}"
STATE_DIR="${AI_ASSET_STATE_DIR:-}"
LANGUAGE="${AI_ASSET_LANGUAGE:-}"
MEMORY_MODE="${AI_ASSET_MEMORY_MODE:-}"
ACTIVITY_SOURCE="${OPENRELIX_ACTIVITY_SOURCE:-${AI_ASSET_ACTIVITY_SOURCE:-auto}}"
STATE_DIR_EXPLICIT=0
if [[ -n "${AI_ASSET_STATE_DIR:-}" ]]; then
  STATE_DIR_EXPLICIT=1
fi

INSTALL_PROFILE="integrated"
INSTALL_GLOBAL_SKILLS=0
INSTALL_CUSTOM_PROMPTS=0
INSTALL_GLOBAL_COMMAND=0
INSTALL_MAC_CLIENT=0
ENABLE_CODEX_MEMORY_SUMMARY=0
ENABLE_MEMORIES=0
DISABLE_CODEX_MEMORIES=0
ENABLE_HISTORY=0
CODEX_MEMORY_SUMMARY_EXPLICIT=0
CODEX_MEMORIES_EXPLICIT=0
CODEX_HISTORY_EXPLICIT=0
MAC_CLIENT_EXPLICIT=0
ENABLE_BACKGROUND_SERVICES=0
ENABLE_NIGHTLY=0
ENABLE_LEARNING_REFRESH=0
ENABLE_UPDATE_CHECK=0
LEARNING_REFRESH_WINDOW_DAYS="${OPENRELIX_REFRESH_LEARN_WINDOW_DAYS:-7}"
MEMORY_MODE_EXPLICIT=0
KEEP_AWAKE="none"
NIGHTLY_ORGANIZE_TIME="${OPENRELIX_NIGHTLY_ORGANIZE_TIME:-23:00}"
NIGHTLY_FINALIZE_TIME="${OPENRELIX_NIGHTLY_FINALIZE_TIME:-00:10}"
UPDATE_CHECK_TIME="${OPENRELIX_UPDATE_CHECK_TIME:-09:30}"
NIGHTLY_ORGANIZE_HOUR=23
NIGHTLY_ORGANIZE_MINUTE=0
NIGHTLY_FINALIZE_HOUR=0
NIGHTLY_FINALIZE_MINUTE=10
UPDATE_CHECK_HOUR=9
UPDATE_CHECK_MINUTE=30
BIN_DIR="${AI_ASSET_BIN_DIR:-}"
SHELL_RC_PATH=""
PATH_EXPORT_ADDED=0
MAC_CLIENT_INSTALLED=0
LAUNCH_AFTER_INSTALL=1
LEARN_AFTER_INSTALL=1
STEP_INDEX=0
TOTAL_STEPS=1
OVERVIEW_RUN_AT_LOAD="<true/>"
USER_APPLICATIONS_DIR="$HOME/Applications"
INSTALLED_MAC_CLIENT_APP="$USER_APPLICATIONS_DIR/OpenRelix.app"

read_project_version() {
  local python_candidate="${PYTHON_BIN:-}"
  if [[ -z "$python_candidate" ]]; then
    python_candidate="$(command -v python3 || true)"
  fi
  if [[ -z "$python_candidate" ]]; then
    return 0
  fi
  "$python_candidate" - "$REPO_ROOT/package.json" 2>/dev/null <<'PY' || true
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        print(json.load(handle).get("version", ""))
except Exception:
    pass
PY
}

PROJECT_VERSION="$(read_project_version)"
if [[ -z "$PROJECT_VERSION" ]]; then
  PROJECT_VERSION="0.0.0"
fi
PROJECT_VERSION_LABEL="v$PROJECT_VERSION"

usage() {
  cat <<'EOF' | sed "s/__OPENRELIX_VERSION_LABEL__/$PROJECT_VERSION_LABEL/g"
Usage:
  ./install/install.sh [options]

Options:
  --profile MODE                Install profile: minimal | integrated. Default: integrated
  --minimal                     Alias for --profile minimal.
  --integrated                  Alias for --profile integrated.
  --state-dir PATH              Override the runtime state root.
  --codex-home PATH             Override CODEX_HOME. Default: ~/.codex
  --codex-bin PATH              Override the Codex CLI binary used by launchd jobs.
                                If omitted, resolved from PATH plus common npm/volta/nvm/brew locations.
  --language CODE               Runtime language: zh | en. Controls panel rendering, local memory
                                storage, and model-generated summaries/next-actions — not just UI strings.
                                If omitted, interactive installs prompt; non-interactive installs default to zh.
  --memory-mode MODE            Memory mode: integrated | local-only | off.
                                Default: integrated.
  --record-memory-only          Record personal memory locally, but disable Codex native context injection.
                                Alias for --memory-mode local-only.
  --use-integrated              Record personal memory and use host context injection.
                                Alias for --memory-mode integrated.
  --disable-personal-memory     Turn off this system's local memory writes.
                                Alias for --memory-mode off.
  --python PATH                 Override the Python binary used by launchd jobs.
  --sync-memory-summary         Explicitly write a bounded summary into CODEX_HOME.
  --no-memory-summary           Skip Codex memory summary sync and keep context injection off.
  --install-global-skills       Symlink the memory-review skill into the user Codex skill root.
  --no-global-skills            Skip global skill symlinks.
  --install-custom-prompts      Install repo-provided Codex custom prompts.
  --no-custom-prompts           Skip Codex custom prompt installation.
  --install-global-command      Install the global `openrelix` command.
  --no-global-command           Skip global `openrelix` command installation.
  --bin-dir PATH                Override the install location for the `openrelix` command.
  --install-mac-client          Build the lightweight OpenRelix.app client.
                                Integrated installs enable this by default on macOS.
  --no-mac-client               Skip macOS client build.
  --no-launch                   Skip the post-install prompt to open the macOS client.
  --no-learn                    Skip the post-install prompt to learn the last 7 days of memory.
  --enable-background-services  Install overview refresh and token-live LaunchAgents.
  --enable-nightly              Install nightly organize/finalize LaunchAgents.
  --enable-update-check         Install a daily no-mutation npm update check LaunchAgent.
  --update-check-time HH:MM     Time for the daily update check. Default: 09:30
  --enable-learning-refresh     Make the 30-minute overview refresh call the
                                Codex adapter and learn memory with a 7-day
                                window. Implies --enable-background-services.
  --disable-learning-refresh    Keep the 30-minute overview refresh no-model.
  --learning-refresh-window-days N
                                Window days for --enable-learning-refresh.
                                Default: 7.
  --disable-background-services Skip overview refresh and token-live LaunchAgents.
  --nightly-organize-time HH:MM Time for same-day nightly preview. Default: 23:00
  --nightly-finalize-time HH:MM Time for previous-day finalize. Default: 00:10
  --keep-awake MODE             Sleep policy for nightly jobs: none | during-job
  --enable-memories             Enable Codex memories config.
  --disable-memories            Do not touch Codex memories config.
  --enable-history              Enable bounded Codex history config.
  --disable-history             Do not touch Codex history config.
  --activity-source SOURCE      Activity source: history | app-server | auto.
                                Default: auto.
                                auto tries Codex app-server first and falls back
                                to history/session files if unavailable.
  --read-codex-app              Alias for --activity-source auto.
                                Kept for compatibility with older install commands.
  -h, --help                    Show this help text.

This __OPENRELIX_VERSION_LABEL__ preview installer currently supports macOS only.
The installer defaults to integrated personal memory: it records into the
configured state root and syncs a bounded summary into Codex native context.
Use --record-memory-only when you explicitly want strict local-only recording
without context injection.
EOF
}

localized_text() {
  local zh_text="$1"
  local en_text="$2"
  if [[ "$LANGUAGE" == "zh" ]]; then
    printf '%s' "$zh_text"
  else
    printf '%s' "$en_text"
  fi
}

step() {
  STEP_INDEX=$((STEP_INDEX + 1))
  printf '[%d/%d] %s\n' "$STEP_INDEX" "$TOTAL_STEPS" "$1"
}

step_done() {
  printf '        %s\n' "$(localized_text "完成" "done")"
}

step_skip() {
  printf '        %s\n' "$(localized_text "已跳过" "skipped")"
}

run_step() {
  local message="$1"
  shift
  step "$message"
  "$@"
  step_done
}

require_option_value() {
  local option="$1"
  local value="${2-}"
  if [[ -z "$value" || "$value" == --* ]]; then
    echo "$option requires a value" >&2
    exit 1
  fi
}

validate_time_option() {
  local option="$1"
  local value="$2"
  if [[ ! "$value" =~ '^([01][0-9]|2[0-3]):[0-5][0-9]$' ]]; then
    echo "Unsupported $option: $value" >&2
    echo "$option must use 24-hour HH:MM format, for example 23:00 or 00:10." >&2
    exit 1
  fi
}

time_hour() {
  local value="$1"
  local hour="${value%%:*}"
  print -r -- "$((10#$hour))"
}

time_minute() {
  local value="$1"
  local minute="${value#*:}"
  print -r -- "$((10#$minute))"
}

detect_install_profile() {
  local -a args=("$@")
  local i=1
  while (( i <= ${#args[@]} )); do
    case "${args[$i]}" in
      --profile)
        require_option_value "--profile" "${args[$((i + 1))]-}"
        INSTALL_PROFILE="${args[$((i + 1))]}"
        i=$((i + 2))
        ;;
      --profile=*)
        INSTALL_PROFILE="${args[$i]#*=}"
        i=$((i + 1))
        ;;
      --minimal)
        INSTALL_PROFILE="minimal"
        i=$((i + 1))
        ;;
      --integrated)
        INSTALL_PROFILE="integrated"
        i=$((i + 1))
        ;;
      *)
        i=$((i + 1))
        ;;
    esac
  done
}

apply_install_profile() {
  case "$INSTALL_PROFILE" in
    minimal)
      ;;
    integrated)
      INSTALL_GLOBAL_SKILLS=1
      INSTALL_CUSTOM_PROMPTS=1
      INSTALL_GLOBAL_COMMAND=1
      INSTALL_MAC_CLIENT=1
      ENABLE_HISTORY=1
      ENABLE_BACKGROUND_SERVICES=1
      ;;
    *)
      echo "Unsupported install profile: $INSTALL_PROFILE" >&2
      echo "Supported profiles: minimal, integrated" >&2
      exit 1
      ;;
  esac
}

detect_shell_rc_path() {
  case "${SHELL##*/}" in
    zsh)
      print -r -- "$HOME/.zshrc"
      ;;
    bash)
      print -r -- "$HOME/.bashrc"
      ;;
    *)
      print -r -- "$HOME/.profile"
      ;;
  esac
}

path_contains_dir() {
  local target="${1:A}"
  local entry=""
  for entry in ${(s/:/)PATH}; do
    [[ -z "$entry" ]] && continue
    if [[ "${entry:A}" == "$target" ]]; then
      return 0
    fi
  done
  return 1
}

choose_bin_dir() {
  local candidate=""
  for candidate in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin" "$HOME/bin"; do
    if [[ -d "$candidate" && -w "$candidate" ]] && path_contains_dir "$candidate"; then
      print -r -- "${candidate:A}"
      return
    fi
  done

  for candidate in /opt/homebrew/bin /usr/local/bin; do
    if [[ -d "$candidate" && -w "$candidate" ]]; then
      print -r -- "${candidate:A}"
      return
    fi
  done

  print -r -- "$HOME/.local/bin"
}

resolve_python_bin() {
  local candidate=""
  local resolved=""
  if [[ -n "$PYTHON_BIN" ]]; then
    print -r -- "$PYTHON_BIN"
    return
  fi

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
      resolved="$(command -v "$candidate")"
      if "$resolved" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
      then
        print -r -- "$resolved"
        return
      fi
    fi
  done
}

select_runtime_language() {
  local answer=""
  if [[ -n "$LANGUAGE" ]]; then
    return
  fi

  if [[ -t 0 && -z "${CI:-}" ]]; then
    print -r -- "Select runtime language / 选择运行语言:"
    print -r -- "  此选择决定面板渲染、本地记忆存储以及大模型生成的 summary / next-action 的语言，"
    print -r -- "  不只是界面文案。安装后切换需要重新跑 installer 并重置已生成的记忆。"
    print -r -- "  This sets the language used by the panel, the local memory store, and the model-generated"
    print -r -- "  summaries / next-actions — not just UI strings. Switching later means rerunning the installer"
    print -r -- "  and re-curating the memory items that were already written."
    print -r -- "  1) 中文 (zh) - default"
    print -r -- "  2) English (en)"
    while true; do
      printf "Language [1/2/zh/en, default zh]: "
      if ! IFS= read -r answer; then
        print -r -- ""
        LANGUAGE="zh"
        return
      fi
      answer="${answer:l}"
      case "$answer" in
        ""|1|zh|zh-cn|zh-hans|cn|chinese|中文)
          LANGUAGE="zh"
          return
          ;;
        2|en|en-us|en-gb|english)
          LANGUAGE="en"
          return
          ;;
        *)
          print -r -- "Please enter 1/zh or 2/en."
          ;;
      esac
    done
  fi

  LANGUAGE="zh"
}

detect_install_profile "$@"
apply_install_profile
if [[ -n "$MEMORY_MODE" ]]; then
  MEMORY_MODE_EXPLICIT=1
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      shift 2
      ;;
    --profile=*)
      shift
      ;;
    --minimal|--integrated)
      shift
      ;;
    --state-dir)
      require_option_value "$1" "${2-}"
      STATE_DIR="$2"
      STATE_DIR_EXPLICIT=1
      shift 2
      ;;
    --codex-home)
      require_option_value "$1" "${2-}"
      CODEX_HOME="$2"
      shift 2
      ;;
    --codex-bin)
      require_option_value "$1" "${2-}"
      CODEX_BIN="$2"
      shift 2
      ;;
    --language)
      require_option_value "$1" "${2-}"
      LANGUAGE="$2"
      shift 2
      ;;
    --language=*)
      LANGUAGE="${1#*=}"
      shift
      ;;
    --memory-mode)
      require_option_value "$1" "${2-}"
      MEMORY_MODE="$2"
      MEMORY_MODE_EXPLICIT=1
      shift 2
      ;;
    --memory-mode=*)
      MEMORY_MODE="${1#*=}"
      MEMORY_MODE_EXPLICIT=1
      shift
      ;;
    --record-memory-only|--local-memory-only)
      MEMORY_MODE="local-only"
      MEMORY_MODE_EXPLICIT=1
      shift
      ;;
    --use-integrated|--use-codex-context)
      MEMORY_MODE="integrated"
      MEMORY_MODE_EXPLICIT=1
      shift
      ;;
    --disable-personal-memory)
      MEMORY_MODE="off"
      MEMORY_MODE_EXPLICIT=1
      shift
      ;;
    --python)
      require_option_value "$1" "${2-}"
      PYTHON_BIN="$2"
      shift 2
      ;;
    --sync-memory-summary)
      ENABLE_CODEX_MEMORY_SUMMARY=1
      CODEX_MEMORY_SUMMARY_EXPLICIT=1
      shift
      ;;
    --no-memory-summary)
      ENABLE_CODEX_MEMORY_SUMMARY=0
      CODEX_MEMORY_SUMMARY_EXPLICIT=1
      shift
      ;;
    --install-global-skills)
      INSTALL_GLOBAL_SKILLS=1
      shift
      ;;
    --no-global-skills)
      INSTALL_GLOBAL_SKILLS=0
      shift
      ;;
    --install-custom-prompts)
      INSTALL_CUSTOM_PROMPTS=1
      shift
      ;;
    --no-custom-prompts)
      INSTALL_CUSTOM_PROMPTS=0
      shift
      ;;
    --install-global-command)
      INSTALL_GLOBAL_COMMAND=1
      shift
      ;;
    --no-global-command)
      INSTALL_GLOBAL_COMMAND=0
      shift
      ;;
    --bin-dir)
      require_option_value "$1" "${2-}"
      BIN_DIR="$2"
      shift 2
      ;;
    --install-mac-client)
      INSTALL_MAC_CLIENT=1
      MAC_CLIENT_EXPLICIT=1
      shift
      ;;
    --no-mac-client|--skip-mac-client)
      INSTALL_MAC_CLIENT=0
      MAC_CLIENT_EXPLICIT=1
      shift
      ;;
    --no-launch|--no-auto-open)
      LAUNCH_AFTER_INSTALL=0
      shift
      ;;
    --no-learn|--no-learn-7d)
      LEARN_AFTER_INSTALL=0
      shift
      ;;
    --enable-background-services)
      ENABLE_BACKGROUND_SERVICES=1
      shift
      ;;
    --enable-nightly)
      ENABLE_NIGHTLY=1
      shift
      ;;
    --enable-update-check)
      ENABLE_UPDATE_CHECK=1
      shift
      ;;
    --disable-update-check)
      ENABLE_UPDATE_CHECK=0
      shift
      ;;
    --update-check-time)
      require_option_value "$1" "${2-}"
      UPDATE_CHECK_TIME="$2"
      shift 2
      ;;
    --update-check-time=*)
      UPDATE_CHECK_TIME="${1#*=}"
      shift
      ;;
    --enable-learning-refresh)
      ENABLE_LEARNING_REFRESH=1
      ENABLE_BACKGROUND_SERVICES=1
      shift
      ;;
    --disable-learning-refresh)
      ENABLE_LEARNING_REFRESH=0
      shift
      ;;
    --learning-refresh-window-days)
      require_option_value "$1" "${2-}"
      LEARNING_REFRESH_WINDOW_DAYS="$2"
      shift 2
      ;;
    --learning-refresh-window-days=*)
      LEARNING_REFRESH_WINDOW_DAYS="${1#*=}"
      shift
      ;;
    --disable-background-services)
      ENABLE_BACKGROUND_SERVICES=0
      ENABLE_LEARNING_REFRESH=0
      shift
      ;;
    --keep-awake)
      require_option_value "$1" "${2-}"
      KEEP_AWAKE="$2"
      shift 2
      ;;
    --keep-awake=*)
      KEEP_AWAKE="${1#*=}"
      shift
      ;;
    --nightly-organize-time)
      require_option_value "$1" "${2-}"
      NIGHTLY_ORGANIZE_TIME="$2"
      shift 2
      ;;
    --nightly-organize-time=*)
      NIGHTLY_ORGANIZE_TIME="${1#*=}"
      shift
      ;;
    --nightly-finalize-time)
      require_option_value "$1" "${2-}"
      NIGHTLY_FINALIZE_TIME="$2"
      shift 2
      ;;
    --nightly-finalize-time=*)
      NIGHTLY_FINALIZE_TIME="${1#*=}"
      shift
      ;;
    --enable-memories)
      ENABLE_MEMORIES=1
      DISABLE_CODEX_MEMORIES=0
      CODEX_MEMORIES_EXPLICIT=1
      shift
      ;;
    --disable-memories)
      ENABLE_MEMORIES=0
      DISABLE_CODEX_MEMORIES=0
      CODEX_MEMORIES_EXPLICIT=1
      shift
      ;;
    --enable-history)
      ENABLE_HISTORY=1
      CODEX_HISTORY_EXPLICIT=1
      shift
      ;;
    --disable-history)
      ENABLE_HISTORY=0
      CODEX_HISTORY_EXPLICIT=1
      shift
      ;;
    --activity-source)
      require_option_value "$1" "${2-}"
      ACTIVITY_SOURCE="$2"
      shift 2
      ;;
    --activity-source=*)
      ACTIVITY_SOURCE="${1#*=}"
      shift
      ;;
    --read-codex-app)
      ACTIVITY_SOURCE="auto"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ "$KEEP_AWAKE" != "none" && "$KEEP_AWAKE" != "during-job" ]]; then
  echo "Unsupported keep-awake mode: $KEEP_AWAKE" >&2
  exit 1
fi
validate_time_option "--nightly-organize-time" "$NIGHTLY_ORGANIZE_TIME"
validate_time_option "--nightly-finalize-time" "$NIGHTLY_FINALIZE_TIME"
validate_time_option "--update-check-time" "$UPDATE_CHECK_TIME"
NIGHTLY_ORGANIZE_HOUR="$(time_hour "$NIGHTLY_ORGANIZE_TIME")"
NIGHTLY_ORGANIZE_MINUTE="$(time_minute "$NIGHTLY_ORGANIZE_TIME")"
NIGHTLY_FINALIZE_HOUR="$(time_hour "$NIGHTLY_FINALIZE_TIME")"
NIGHTLY_FINALIZE_MINUTE="$(time_minute "$NIGHTLY_FINALIZE_TIME")"
UPDATE_CHECK_HOUR="$(time_hour "$UPDATE_CHECK_TIME")"
UPDATE_CHECK_MINUTE="$(time_minute "$UPDATE_CHECK_TIME")"

case "$ACTIVITY_SOURCE" in
  history|app-server|auto)
    ;;
  *)
    echo "Unsupported activity source: $ACTIVITY_SOURCE" >&2
    echo "Supported activity sources: history, app-server, auto" >&2
    exit 1
    ;;
esac

if [[ "$OSTYPE" != darwin* ]]; then
  echo "OpenRelix $PROJECT_VERSION_LABEL preview installer currently supports macOS only." >&2
  echo "Set AI_ASSET_STATE_DIR and run lower-level scripts manually if you are experimenting on another platform." >&2
  exit 1
fi

select_runtime_language

PYTHON_BIN="$(resolve_python_bin)"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "missing Python 3.10+ interpreter" >&2
  exit 1
fi
if ! "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
then
  echo "Python must be 3.10+ for this installer: $PYTHON_BIN" >&2
  exit 1
fi

# Resolve the Codex CLI binary so LaunchAgents (which run with a narrow PATH)
# can still reach app-server. Falls back to default_codex_binary() candidates,
# which include npm-global/volta/nvm/brew locations.
if [[ -z "$CODEX_BIN" ]]; then
  CODEX_BIN="$(
    CODEX_BIN="" "$PYTHON_BIN" - "$REPO_ROOT" <<'PY'
import os
import sys

repo_root = sys.argv[1]
sys.path.insert(0, repo_root + "/scripts")

os.environ.pop("CODEX_BIN", None)
from asset_runtime import default_codex_binary  # noqa: E402

print(default_codex_binary())
PY
  )"
fi
if [[ -z "$CODEX_BIN" || ! -x "$CODEX_BIN" ]]; then
  echo "Could not locate the Codex CLI binary." >&2
  echo "Install Codex CLI (e.g. \`npm install -g @openai/codex\`) or pass --codex-bin /full/path/to/codex." >&2
  exit 1
fi
CODEX_BIN_DIR="${CODEX_BIN:h}"
SAFE_PATH="$CODEX_BIN_DIR:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

if (( ! STATE_DIR_EXPLICIT )); then
  STATE_DIR="$(
    "$PYTHON_BIN" - "$REPO_ROOT" <<'PY'
import sys

repo_root = sys.argv[1]
sys.path.insert(0, repo_root + "/scripts")

from asset_runtime import default_state_root  # noqa: E402

print(default_state_root())
PY
  )"
fi

LANGUAGE="$(
  "$PYTHON_BIN" - "$REPO_ROOT" "$LANGUAGE" <<'PY'
import sys

repo_root = sys.argv[1]
language = sys.argv[2]
sys.path.insert(0, repo_root + "/scripts")

from asset_runtime import normalize_language  # noqa: E402

try:
    print(normalize_language(language, strict=True))
except ValueError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1)
PY
)"

MEMORY_MODE="$(
  "$PYTHON_BIN" - "$REPO_ROOT" "$MEMORY_MODE" <<'PY'
import sys

repo_root = sys.argv[1]
memory_mode = sys.argv[2]
sys.path.insert(0, repo_root + "/scripts")

from asset_runtime import normalize_memory_mode  # noqa: E402

try:
    print(normalize_memory_mode(memory_mode, strict=bool(memory_mode)))
except ValueError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1)
PY
)"

if (( CODEX_MEMORY_SUMMARY_EXPLICIT )) && (( ! ENABLE_CODEX_MEMORY_SUMMARY )) && [[ "$MEMORY_MODE" == "integrated" ]]; then
  MEMORY_MODE="local-only"
fi

case "$MEMORY_MODE" in
  local-only)
    if (( ! CODEX_MEMORY_SUMMARY_EXPLICIT )); then
      ENABLE_CODEX_MEMORY_SUMMARY=0
    fi
    if (( ! CODEX_HISTORY_EXPLICIT )); then
      ENABLE_HISTORY=1
    fi
    if (( ! CODEX_MEMORIES_EXPLICIT )); then
      ENABLE_MEMORIES=0
      DISABLE_CODEX_MEMORIES=1
    fi
    ;;
  integrated)
    if (( ! CODEX_MEMORY_SUMMARY_EXPLICIT )); then
      ENABLE_CODEX_MEMORY_SUMMARY=1
    fi
    if (( ! CODEX_MEMORIES_EXPLICIT )); then
      ENABLE_MEMORIES=1
      DISABLE_CODEX_MEMORIES=0
    fi
    if (( ! CODEX_HISTORY_EXPLICIT )); then
      ENABLE_HISTORY=1
    fi
    ;;
  off)
    if (( ! CODEX_MEMORY_SUMMARY_EXPLICIT )); then
      ENABLE_CODEX_MEMORY_SUMMARY=0
    fi
    if (( ! CODEX_MEMORIES_EXPLICIT )); then
      ENABLE_MEMORIES=0
      DISABLE_CODEX_MEMORIES=0
    fi
    if (( ! CODEX_HISTORY_EXPLICIT )); then
      ENABLE_HISTORY=0
    fi
    ;;
esac

if ! [[ "$LEARNING_REFRESH_WINDOW_DAYS" =~ '^[0-9]+$' ]]; then
  echo "--learning-refresh-window-days must be a non-negative integer: $LEARNING_REFRESH_WINDOW_DAYS" >&2
  exit 1
fi
if (( ENABLE_LEARNING_REFRESH )); then
  OVERVIEW_RUN_AT_LOAD="<false/>"
fi

if (( INSTALL_GLOBAL_COMMAND )); then
  if [[ -z "$BIN_DIR" ]]; then
    BIN_DIR="$(choose_bin_dir)"
  fi
  BIN_DIR="${BIN_DIR:A}"
  SHELL_RC_PATH="$(detect_shell_rc_path)"
fi

if (( ENABLE_CODEX_MEMORY_SUMMARY )); then
  TOTAL_STEPS=$((TOTAL_STEPS + 1))
fi
if (( DISABLE_CODEX_MEMORIES || ENABLE_MEMORIES || ENABLE_HISTORY )); then
  TOTAL_STEPS=$((TOTAL_STEPS + 1))
fi
if (( INSTALL_GLOBAL_SKILLS )); then
  TOTAL_STEPS=$((TOTAL_STEPS + 1))
fi
if (( INSTALL_CUSTOM_PROMPTS )); then
  TOTAL_STEPS=$((TOTAL_STEPS + 1))
fi
if (( INSTALL_GLOBAL_COMMAND )); then
  TOTAL_STEPS=$((TOTAL_STEPS + 1))
fi
if [[ "$OSTYPE" == darwin* ]] && (( INSTALL_MAC_CLIENT )); then
  TOTAL_STEPS=$((TOTAL_STEPS + 1))
fi
if [[ "$OSTYPE" == darwin* ]] && (( ENABLE_BACKGROUND_SERVICES )); then
  TOTAL_STEPS=$((TOTAL_STEPS + 1))
fi
if [[ "$OSTYPE" == darwin* ]] && (( ENABLE_NIGHTLY )); then
  TOTAL_STEPS=$((TOTAL_STEPS + 1))
fi
if [[ "$OSTYPE" == darwin* ]] && (( ENABLE_UPDATE_CHECK )); then
  TOTAL_STEPS=$((TOTAL_STEPS + 1))
fi

render_plist() {
  local template_name="$1"
  local output_path="$2"
  "$PYTHON_BIN" "$REPO_ROOT/install/render_template.py" \
    --template "$REPO_ROOT/ops/launchd/${template_name}" \
    --output "$output_path" \
    --set "REPO_ROOT=$REPO_ROOT" \
    --set "STATE_ROOT=$STATE_DIR" \
    --set "PYTHON_BIN=$PYTHON_BIN" \
    --set "CODEX_BIN=$CODEX_BIN" \
    --set "CODEX_HOME=$CODEX_HOME" \
    --set "SAFE_PATH=$SAFE_PATH" \
    --set "ACTIVITY_SOURCE=$ACTIVITY_SOURCE" \
    --set "LEARNING_REFRESH=$ENABLE_LEARNING_REFRESH" \
    --set "LEARNING_REFRESH_WINDOW_DAYS=$LEARNING_REFRESH_WINDOW_DAYS" \
    --set "OVERVIEW_RUN_AT_LOAD=$OVERVIEW_RUN_AT_LOAD" \
    --set "KEEP_AWAKE=$KEEP_AWAKE" \
    --set "NIGHTLY_ORGANIZE_HOUR=$NIGHTLY_ORGANIZE_HOUR" \
    --set "NIGHTLY_ORGANIZE_MINUTE=$NIGHTLY_ORGANIZE_MINUTE" \
    --set "NIGHTLY_FINALIZE_HOUR=$NIGHTLY_FINALIZE_HOUR" \
    --set "NIGHTLY_FINALIZE_MINUTE=$NIGHTLY_FINALIZE_MINUTE" \
    --set "UPDATE_CHECK_HOUR=$UPDATE_CHECK_HOUR" \
    --set "UPDATE_CHECK_MINUTE=$UPDATE_CHECK_MINUTE"
}

bootstrap_launch_agent() {
  local plist_path="$1"
  local label="$2"
  local kickstart="${3:-1}"
  local previous_public_prefix="io.github.open""keepsake"
  local legacy_prefix=""
  local legacy_label=""
  local legacy_plist=""
  for legacy_prefix in "$previous_public_prefix" io.github.ai-personal-assets io.github.codex-personal-assets; do
    legacy_label="${label/io.github.openrelix/$legacy_prefix}"
    legacy_plist="$HOME/Library/LaunchAgents/${legacy_label}.plist"
    [[ "$legacy_label" == "$label" ]] && continue
    launchctl bootout "gui/$(id -u)/$legacy_label" >/dev/null 2>&1 || true
    if [[ -f "$legacy_plist" ]]; then
      launchctl bootout "gui/$(id -u)" "$legacy_plist" >/dev/null 2>&1 || true
      rm -f "$legacy_plist"
    fi
  done
  /usr/bin/plutil -lint "$plist_path" >/dev/null
  launchctl bootout "gui/$(id -u)" "$plist_path" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$plist_path"
  if [[ "$kickstart" == "1" ]]; then
    launchctl kickstart -k "gui/$(id -u)/$label" >/dev/null 2>&1 || true
  fi
}

if (( ENABLE_CODEX_MEMORY_SUMMARY || DISABLE_CODEX_MEMORIES || ENABLE_MEMORIES || ENABLE_HISTORY || INSTALL_GLOBAL_SKILLS || INSTALL_CUSTOM_PROMPTS )); then
  mkdir -p "$CODEX_HOME"
fi
export AI_ASSET_STATE_DIR="$STATE_DIR"
export CODEX_HOME="$CODEX_HOME"
export PYTHON_BIN="$PYTHON_BIN"
export AI_ASSET_LANGUAGE="$LANGUAGE"
export AI_ASSET_MEMORY_MODE="$MEMORY_MODE"
export OPENRELIX_ACTIVITY_SOURCE="$ACTIVITY_SOURCE"

initialize_state_root() {
  "$PYTHON_BIN" - "$REPO_ROOT" "$LANGUAGE" "$MEMORY_MODE" "$ACTIVITY_SOURCE" <<'PY'
import sys

repo_root = sys.argv[1]
language = sys.argv[2]
memory_mode = sys.argv[3]
activity_source = sys.argv[4]
sys.path.insert(0, repo_root + "/scripts")

from asset_runtime import ensure_state_layout, write_runtime_config  # noqa: E402

paths = ensure_state_layout()
write_runtime_config(
    language=language,
    memory_mode=memory_mode,
    activity_source=activity_source,
    paths=paths,
)
PY
  "$PYTHON_BIN" "$REPO_ROOT/scripts/build_overview.py"
  "$PYTHON_BIN" - "$REPO_ROOT" "$LANGUAGE" <<'PY'
import json
import sys

repo_root = sys.argv[1]
expected_language = sys.argv[2]
sys.path.insert(0, repo_root + "/scripts")

from asset_runtime import get_runtime_paths, load_runtime_config  # noqa: E402

paths = get_runtime_paths()
config = load_runtime_config(paths)
overview_path = paths.reports_dir / "overview-data.json"
panel_path = paths.reports_dir / "panel.html"

errors = []
if config.get("language") != expected_language:
    errors.append(
        "runtime/config.json language={} expected={}".format(
            config.get("language"),
            expected_language,
        )
    )

try:
    overview = json.loads(overview_path.read_text(encoding="utf-8"))
except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
    errors.append("overview-data.json is not readable: {}".format(exc))
else:
    if overview.get("language") != expected_language:
        errors.append(
            "overview-data.json language={} expected={}".format(
                overview.get("language"),
                expected_language,
            )
        )

try:
    panel_html = panel_path.read_text(encoding="utf-8")
except (OSError, UnicodeDecodeError) as exc:
    errors.append("panel.html is not readable: {}".format(exc))
else:
    marker = 'data-default-language="{}"'.format(expected_language)
    if marker not in panel_html:
        errors.append("panel.html missing {}".format(marker))

if errors:
    print("runtime language sync verification failed:", file=sys.stderr)
    for error in errors:
        print("- {}".format(error), file=sys.stderr)
    raise SystemExit(1)
PY
}

run_step "$(localized_text "初始化状态目录、语言配置和第一份概览..." "Initializing state root, language config, and first overview...")" \
  initialize_state_root

if (( ENABLE_CODEX_MEMORY_SUMMARY )); then
  run_step "$(localized_text "同步受控的 Codex 记忆摘要..." "Syncing the bounded Codex memory summary...")" \
    "$PYTHON_BIN" "$REPO_ROOT/scripts/build_codex_memory_summary.py" \
    --memory-summary "$CODEX_HOME/memories/memory_summary.md"
fi

config_args=()
if (( DISABLE_CODEX_MEMORIES )); then
  config_args+=(--disable-codex-memories)
elif (( ENABLE_MEMORIES )); then
  config_args+=(--enable-memories)
fi
if (( ENABLE_HISTORY )); then
  config_args+=(--enable-history --history-max-bytes 268435456)
fi
if (( ${#config_args[@]} > 0 )); then
  run_step "$(localized_text "配置 Codex 用户设置..." "Configuring Codex user settings...")" \
    "$PYTHON_BIN" "$REPO_ROOT/install/configure_codex_user.py" \
    --config "$CODEX_HOME/config.toml" \
    "${config_args[@]}"
fi

if (( INSTALL_GLOBAL_SKILLS )); then
  step "$(localized_text "把 memory-review 链接到用户 Codex skill 目录..." "Linking memory-review into the user Codex skill directory...")"
  mkdir -p "$CODEX_HOME/skills"
  ln -sfn "$REPO_ROOT/.agents/skills/memory-review" \
    "$CODEX_HOME/skills/memory-review"
  step_done
fi

if (( INSTALL_CUSTOM_PROMPTS )); then
  step "$(localized_text "安装 Codex 自定义提示词..." "Installing Codex custom prompts...")"
  mkdir -p "$CODEX_HOME/prompts"
  "$PYTHON_BIN" "$REPO_ROOT/install/render_template.py" \
    --template "$REPO_ROOT/install/templates/codex-prompts/memory-review.md.tmpl" \
    --output "$CODEX_HOME/prompts/memory-review.md" \
    --set "REPO_ROOT=$REPO_ROOT" \
    --set "STATE_ROOT=$STATE_DIR"
  step_done
fi

if (( INSTALL_GLOBAL_COMMAND )); then
  step "$(localized_text "安装全局 openrelix 命令..." "Installing the global openrelix command...")"
  mkdir -p "$BIN_DIR"
  "$PYTHON_BIN" "$REPO_ROOT/install/render_template.py" \
    --template "$REPO_ROOT/install/templates/bin/openrelix.tmpl" \
    --output "$BIN_DIR/openrelix" \
    --set "REPO_ROOT=$REPO_ROOT" \
    --set "STATE_ROOT=$STATE_DIR" \
    --set "CODEX_HOME=$CODEX_HOME" \
    --set "PYTHON_BIN=$PYTHON_BIN" \
    --set "ACTIVITY_SOURCE=$ACTIVITY_SOURCE"
  chmod +x "$BIN_DIR/openrelix"
  if ! path_contains_dir "$BIN_DIR"; then
    "$PYTHON_BIN" "$REPO_ROOT/install/configure_shell_path.py" \
      --config "$SHELL_RC_PATH" \
      --path-entry "$BIN_DIR"
    PATH_EXPORT_ADDED=1
  fi
  step_done
fi

if [[ "$OSTYPE" == darwin* ]] && (( INSTALL_MAC_CLIENT )); then
  step "$(localized_text "安装轻量 macOS 客户端..." "Installing the lightweight macOS client...")"
  if [[ ! -x "$REPO_ROOT/scripts/build_macos_client.sh" ]]; then
    if (( MAC_CLIENT_EXPLICIT )); then
      echo "$(localized_text "缺少 macOS 客户端构建脚本" "Missing macOS client builder"): $REPO_ROOT/scripts/build_macos_client.sh" >&2
      exit 1
    fi
    printf '        %s\n' "$(localized_text "缺少构建脚本；已跳过" "missing builder; skipped")"
    step_skip
  elif ! command -v swiftc >/dev/null 2>&1; then
    if (( MAC_CLIENT_EXPLICIT )); then
      echo "$(localized_text "缺少 swiftc。请先安装 Xcode Command Line Tools：xcode-select --install" "Missing swiftc. Install Xcode Command Line Tools first: xcode-select --install")" >&2
      exit 1
    fi
    printf '        %s\n' "$(localized_text "未找到 swiftc；已跳过" "swiftc not found; skipped")"
    step_skip
  elif ! command -v ditto >/dev/null 2>&1; then
    if (( MAC_CLIENT_EXPLICIT )); then
      echo "$(localized_text "缺少 ditto，无法把 macOS 客户端安装到用户应用目录。" "Missing ditto; cannot install the macOS client into the user Applications directory.")" >&2
      exit 1
    fi
    printf '        %s\n' "$(localized_text "未找到 ditto；已跳过" "ditto not found; skipped")"
    step_skip
  else
    "$REPO_ROOT/scripts/build_macos_client.sh" \
      --output "$STATE_DIR/runtime/mac-app/OpenRelix.app" \
      --state-root "$STATE_DIR"
    MAC_CLIENT_INSTALLED=1
    mkdir -p "$USER_APPLICATIONS_DIR"
    rm -rf "$INSTALLED_MAC_CLIENT_APP"
    ditto "$STATE_DIR/runtime/mac-app/OpenRelix.app" "$INSTALLED_MAC_CLIENT_APP"
    LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
    if [[ -x "$LSREGISTER" ]]; then
      "$LSREGISTER" -f "$INSTALLED_MAC_CLIENT_APP" >/dev/null 2>&1 || true
    fi
    printf '        %s\n' "$(localized_text "已同步到用户应用目录: $INSTALLED_MAC_CLIENT_APP" "Synced to user Applications: $INSTALLED_MAC_CLIENT_APP")"
    step_done
  fi
fi

if [[ "$OSTYPE" == darwin* ]] && (( ENABLE_BACKGROUND_SERVICES || ENABLE_NIGHTLY || ENABLE_UPDATE_CHECK )); then
  mkdir -p "$HOME/Library/LaunchAgents"

  if (( ENABLE_BACKGROUND_SERVICES )); then
    step "$(localized_text "安装后台刷新服务..." "Installing background refresh services...")"
    render_plist \
      "io.github.openrelix.overview-refresh.plist.tmpl" \
      "$HOME/Library/LaunchAgents/io.github.openrelix.overview-refresh.plist"
    bootstrap_launch_agent \
      "$HOME/Library/LaunchAgents/io.github.openrelix.overview-refresh.plist" \
      "io.github.openrelix.overview-refresh" \
      "$(( ENABLE_LEARNING_REFRESH ? 0 : 1 ))"

    render_plist \
      "io.github.openrelix.token-live.plist.tmpl" \
      "$HOME/Library/LaunchAgents/io.github.openrelix.token-live.plist"
    bootstrap_launch_agent \
      "$HOME/Library/LaunchAgents/io.github.openrelix.token-live.plist" \
      "io.github.openrelix.token-live"
    step_done
  fi

  if (( ENABLE_NIGHTLY )); then
    step "$(localized_text "安装夜间整理服务..." "Installing nightly organization services...")"
    render_plist \
      "io.github.openrelix.nightly-organize.plist.tmpl" \
      "$HOME/Library/LaunchAgents/io.github.openrelix.nightly-organize.plist"
    bootstrap_launch_agent \
      "$HOME/Library/LaunchAgents/io.github.openrelix.nightly-organize.plist" \
      "io.github.openrelix.nightly-organize"

    render_plist \
      "io.github.openrelix.nightly-finalize-previous-day.plist.tmpl" \
      "$HOME/Library/LaunchAgents/io.github.openrelix.nightly-finalize-previous-day.plist"
    bootstrap_launch_agent \
      "$HOME/Library/LaunchAgents/io.github.openrelix.nightly-finalize-previous-day.plist" \
      "io.github.openrelix.nightly-finalize-previous-day"
    step_done
  fi

  if (( ENABLE_UPDATE_CHECK )); then
    step "$(localized_text "安装每日更新检查服务..." "Installing daily update check service...")"
    render_plist \
      "io.github.openrelix.update-check.plist.tmpl" \
      "$HOME/Library/LaunchAgents/io.github.openrelix.update-check.plist"
    bootstrap_launch_agent \
      "$HOME/Library/LaunchAgents/io.github.openrelix.update-check.plist" \
      "io.github.openrelix.update-check" \
      0
    step_done
  fi
fi

learn_memory_command() {
  if (( INSTALL_GLOBAL_COMMAND )); then
    printf 'openrelix refresh --learn-memory --learn-window-days %s\n' "$LEARNING_REFRESH_WINDOW_DAYS"
    return
  fi
  printf 'AI_ASSET_STATE_DIR=%q CODEX_HOME=%q AI_ASSET_LANGUAGE=%q OPENRELIX_ACTIVITY_SOURCE=%q %q %q refresh --learn-memory --learn-window-days %s\n' \
    "$STATE_DIR" \
    "$CODEX_HOME" \
    "$LANGUAGE" \
    "$ACTIVITY_SOURCE" \
    "$PYTHON_BIN" \
    "$REPO_ROOT/scripts/openrelix.py" \
    "$LEARNING_REFRESH_WINDOW_DAYS"
}

open_panel_command() {
  if [[ "$OSTYPE" == darwin* ]] && (( INSTALL_MAC_CLIENT )); then
    if (( INSTALL_GLOBAL_COMMAND )); then
      printf 'openrelix app\n'
      return
    fi
    printf 'open %q\n' "$INSTALLED_MAC_CLIENT_APP"
    return
  fi
  if (( INSTALL_GLOBAL_COMMAND )); then
    printf 'openrelix open panel\n'
    return
  fi
  printf 'open %q\n' "$STATE_DIR/reports/panel.html"
}

mac_app_command() {
  if (( INSTALL_GLOBAL_COMMAND )); then
    printf 'openrelix app'
  else
    printf 'open %q' "$INSTALLED_MAC_CLIENT_APP"
  fi
}

web_panel_command() {
  if (( INSTALL_GLOBAL_COMMAND )); then
    printf 'openrelix open panel'
  else
    printf 'open %q' "$STATE_DIR/reports/panel.html"
  fi
}

is_ci_environment() {
  [[ -n "${CI:-}" && "${CI:-}" != "0" && "${CI:-}" != "false" ]] || \
    [[ -n "${OPENRELIX_NO_LAUNCH:-}" && "${OPENRELIX_NO_LAUNCH:-}" != "0" && "${OPENRELIX_NO_LAUNCH:-}" != "false" ]]
}

LEARN_MEMORY_COMMAND="$(learn_memory_command)"
OPEN_PANEL_COMMAND="$(open_panel_command)"
MAC_APP_COMMAND="$(mac_app_command)"
WEB_PANEL_COMMAND="$(web_panel_command)"
WILL_AUTO_LAUNCH=0
if [[ "$OSTYPE" == darwin* ]] && (( MAC_CLIENT_INSTALLED )) && (( LAUNCH_AFTER_INSTALL )) && ! is_ci_environment; then
  WILL_AUTO_LAUNCH=1
fi
if [[ "$MEMORY_MODE" == "integrated" ]]; then
  REVIEW_CONTEXT_NOTE_ZH="这一步会显式调用当前 Codex 适配器，学习今日和最近 ${LEARNING_REFRESH_WINDOW_DAYS} 天窗口，随后生成本地 memory / overview。当前 integrated 会同步 bounded summary，但不会把原始窗口写进 Codex 原生 memory。"
  REVIEW_CONTEXT_NOTE_EN="This explicitly calls the current Codex adapter, learns from today plus the last ${LEARNING_REFRESH_WINDOW_DAYS} days of windows, then updates local memory / overview. The current integrated mode syncs a bounded summary, but does not write raw windows into Codex native memory."
else
  REVIEW_CONTEXT_NOTE_ZH="这一步会显式调用当前 Codex 适配器，学习今日和最近 ${LEARNING_REFRESH_WINDOW_DAYS} 天窗口，随后生成本地 memory / overview。当前 $MEMORY_MODE 不会向 Codex context 同步摘要。"
  REVIEW_CONTEXT_NOTE_EN="This explicitly calls the current Codex adapter, learns from today plus the last ${LEARNING_REFRESH_WINDOW_DAYS} days of windows, then updates local memory / overview. The current $MEMORY_MODE mode does not sync a summary into Codex context."
fi

if [[ "$LANGUAGE" == "zh" ]]; then
  cat <<EOF
OpenRelix 已安装完成。

安装信息：
  安装模式: $INSTALL_PROFILE
  源码目录: $REPO_ROOT
  状态目录: $STATE_DIR
  Codex 目录: $CODEX_HOME
  语言: $LANGUAGE
  记忆模式: $MEMORY_MODE
  活动来源: $ACTIVITY_SOURCE
  面板: $STATE_DIR/reports/panel.html

建议下一步：
EOF
  if [[ "$OSTYPE" == darwin* ]] && (( INSTALL_MAC_CLIENT )); then
    cat <<EOF
  1. 任何时候都可以用这两条指令打开 OpenRelix：
     $MAC_APP_COMMAND        # 原生 macOS 客户端
     $WEB_PANEL_COMMAND      # 浏览器中的可视化面板
EOF
  else
    cat <<EOF
  1. 打开可视化面板：
     $OPEN_PANEL_COMMAND
EOF
  fi

  if (( ENABLE_LEARNING_REFRESH )); then
    cat <<EOF
  2. 已开启 30 分钟自动学习刷新；首次自动学习会在下一个 30 分钟周期运行。
     默认会先尝试 Codex app-server，失败时回退 CLI history/session；如需只读稳定 CLI 文件，安装时加 --activity-source history。
EOF
  else
    cat <<EOF
  2. 推荐：安装后立刻学习今日和最近 ${LEARNING_REFRESH_WINDOW_DAYS} 天窗口，刷新本地记忆：
     $LEARN_MEMORY_COMMAND
     $REVIEW_CONTEXT_NOTE_ZH
     默认会先尝试 Codex app-server，失败时回退 CLI history/session；如需只读稳定 CLI 文件，安装时加 --activity-source history。
EOF
  fi

  if (( INSTALL_GLOBAL_SKILLS )); then
    cat <<EOF
  3. 在新的 Codex 线程里，需要临时复盘任务时可以输入 /memory-review。
EOF
  fi

  if (( INSTALL_CUSTOM_PROMPTS )); then
    cat <<EOF
  4. 如果当前 Codex 版本 custom prompt 更稳定，也可以用 /prompts:memory-review 作为兼容入口。
EOF
  fi

  if (( INSTALL_GLOBAL_COMMAND )); then
    cat <<EOF

Shell 入口：
  $BIN_DIR/openrelix
  常用命令：openrelix open panel、openrelix app、openrelix core、openrelix update --check、openrelix update --yes
EOF
  fi

  if [[ "$OSTYPE" == darwin* ]] && (( ENABLE_NIGHTLY )); then
    cat <<EOF

后台整理：
  已安装 nightly LaunchAgents：$NIGHTLY_ORGANIZE_TIME 预览整理，$NIGHTLY_FINALIZE_TIME 回补前一天终版整理。
  锁屏可以继续跑；退出登录后用户级 LaunchAgents 不会继续执行。
EOF
  fi

  if [[ "$OSTYPE" == darwin* ]] && (( ENABLE_BACKGROUND_SERVICES )); then
    if (( ENABLE_LEARNING_REFRESH )); then
      cat <<EOF

后台刷新：
  overview-refresh 已安装为每 30 分钟自动学习刷新一次，会调用当前 Codex 适配器并使用最近 ${LEARNING_REFRESH_WINDOW_DAYS} 天窗口。
EOF
    else
      cat <<EOF

后台刷新：
  overview-refresh 已安装为每 30 分钟刷新一次；当前保持 no-model。如需自动学习刷新，重新安装时加 --enable-learning-refresh。
EOF
    fi
  fi

  if [[ "$OSTYPE" == darwin* ]] && (( ENABLE_UPDATE_CHECK )); then
    cat <<EOF

更新检查：
  已安装每日更新检查 LaunchAgent：每天 $UPDATE_CHECK_TIME 运行 openrelix update --check。
  它只检查 npm 最新版本并写入日志，不会自动安装；需要升级时手动运行 openrelix update --yes。
EOF
  fi
else
  cat <<EOF
Installed OpenRelix.

Install info:
  Profile: $INSTALL_PROFILE
  Repo root: $REPO_ROOT
  State root: $STATE_DIR
  Codex home: $CODEX_HOME
  Language: $LANGUAGE
  Memory mode: $MEMORY_MODE
  Activity source: $ACTIVITY_SOURCE
  Panel: $STATE_DIR/reports/panel.html

Recommended next steps:
EOF
  if [[ "$OSTYPE" == darwin* ]] && (( INSTALL_MAC_CLIENT )); then
    cat <<EOF
  1. Use these commands anytime to open OpenRelix:
     $MAC_APP_COMMAND        # native macOS client
     $WEB_PANEL_COMMAND      # visual panel in your browser
EOF
  else
    cat <<EOF
  1. Open the visual panel:
     $OPEN_PANEL_COMMAND
EOF
  fi

  if (( ENABLE_LEARNING_REFRESH )); then
    cat <<EOF
  2. Automatic learning refresh is enabled; the first learning run will happen on the next 30-minute interval.
     By default, OpenRelix tries Codex app-server first and falls back to CLI history/session; add --activity-source history to force stable CLI files only.
EOF
  else
    cat <<EOF
  2. Recommended: learn from today plus the last ${LEARNING_REFRESH_WINDOW_DAYS} days of windows and refresh local memory:
     $LEARN_MEMORY_COMMAND
     $REVIEW_CONTEXT_NOTE_EN
     By default, OpenRelix tries Codex app-server first and falls back to CLI history/session; add --activity-source history to force stable CLI files only.
EOF
  fi

  if (( INSTALL_GLOBAL_SKILLS )); then
    cat <<EOF
  3. In a new Codex thread, type /memory-review only when you need an immediate task review.
EOF
  fi

  if (( INSTALL_CUSTOM_PROMPTS )); then
    cat <<EOF
  4. /prompts:memory-review remains available as a compatibility fallback on Codex versions that load custom prompts reliably.
EOF
  fi

  if (( INSTALL_GLOBAL_COMMAND )); then
    cat <<EOF

Shell entrypoint:
  $BIN_DIR/openrelix
  Common commands: openrelix open panel, openrelix app, openrelix core, openrelix update --check, openrelix update --yes
EOF
  fi

  if [[ "$OSTYPE" == darwin* ]] && (( ENABLE_NIGHTLY )); then
    cat <<EOF

Background organization:
  Nightly LaunchAgents are installed: preview at $NIGHTLY_ORGANIZE_TIME and previous-day finalize at $NIGHTLY_FINALIZE_TIME.
  A locked screen is fine; logging out stops user-level LaunchAgents.
EOF
  fi

  if [[ "$OSTYPE" == darwin* ]] && (( ENABLE_BACKGROUND_SERVICES )); then
    if (( ENABLE_LEARNING_REFRESH )); then
      cat <<EOF

Background refresh:
  overview-refresh is installed to learn automatically every 30 minutes. It calls the current Codex adapter with the last ${LEARNING_REFRESH_WINDOW_DAYS} days of windows.
EOF
    else
      cat <<EOF

Background refresh:
  overview-refresh is installed to refresh every 30 minutes in no-model mode. Reinstall with --enable-learning-refresh for automatic learning refresh.
EOF
    fi
  fi

  if [[ "$OSTYPE" == darwin* ]] && (( ENABLE_UPDATE_CHECK )); then
    cat <<EOF

Update check:
  Daily update check LaunchAgent installed: openrelix update --check runs at $UPDATE_CHECK_TIME.
  It only checks the latest npm version and writes logs; run openrelix update --yes manually when you want to upgrade.
EOF
  fi
fi

if (( INSTALL_GLOBAL_COMMAND )) && (( PATH_EXPORT_ADDED )); then
  if [[ "$LANGUAGE" == "zh" ]]; then
    cat <<EOF

PATH 提示：
  installer 已把 $BIN_DIR 写入:
    $SHELL_RC_PATH

当前 shell 里如果马上要用 \`openrelix\`，先执行：
  export PATH="$BIN_DIR:\$PATH"
EOF
  else
    cat <<EOF

PATH note:
  The installer added $BIN_DIR to PATH in:
    $SHELL_RC_PATH

To use \`openrelix\` in the current shell immediately, run:
  export PATH="$BIN_DIR:\$PATH"
EOF
  fi
fi

INTERACTIVE_TTY=0
if [[ -t 0 && -t 1 && -z "${CI:-}" ]]; then
  INTERACTIVE_TTY=1
fi

is_yes_answer() {
  local value="${1:l}"
  case "$value" in
    y|yes|是|是的|好|好的|1) return 0 ;;
  esac
  return 1
}

is_no_answer() {
  local value="${1:l}"
  case "$value" in
    n|no|否|不|不要|0) return 0 ;;
  esac
  return 1
}

if (( INTERACTIVE_TTY )) && (( LEARN_AFTER_INSTALL )); then
  if [[ "$LANGUAGE" == "en" ]]; then
    print -r -- ""
    print -r -- "Learn the last 7 days of memory now? This calls Codex on your"
    print -r -- "behalf and may take 5–15 minutes. The command that will run is:"
    print -r -- "  openrelix review --stage final --learn-window-days 7"
    printf "Run it now? [y/N]: "
  else
    print -r -- ""
    print -r -- "现在学习最近 7 天的记忆吗？这一步会调用 Codex 生成本地记忆，"
    print -r -- "预计 5–15 分钟。将要执行的命令是："
    print -r -- "  openrelix review --stage final --learn-window-days 7"
    printf "是否执行？[y/N]: "
  fi
  LEARN_ANSWER=""
  IFS= read -r LEARN_ANSWER || LEARN_ANSWER=""
  if is_yes_answer "$LEARN_ANSWER"; then
    AI_ASSET_STATE_DIR="$STATE_DIR" \
      CODEX_HOME="$CODEX_HOME" \
      AI_ASSET_LANGUAGE="$LANGUAGE" \
      OPENRELIX_ACTIVITY_SOURCE="$ACTIVITY_SOURCE" \
      "$PYTHON_BIN" "$REPO_ROOT/scripts/openrelix.py" \
      review --stage final --learn-window-days 7 || true
  fi
fi

if (( INTERACTIVE_TTY )) && (( WILL_AUTO_LAUNCH )); then
  APP_LAUNCH_PATH="$INSTALLED_MAC_CLIENT_APP"
  if [[ -d "$APP_LAUNCH_PATH" ]]; then
    if [[ "$LANGUAGE" == "en" ]]; then
      printf $'\nOpen the OpenRelix client now? [Y/n]: '
    else
      printf $'\n现在打开 OpenRelix 客户端吗？[Y/n]: '
    fi
    LAUNCH_ANSWER=""
    IFS= read -r LAUNCH_ANSWER || LAUNCH_ANSWER=""
    if [[ -z "$LAUNCH_ANSWER" ]] || is_yes_answer "$LAUNCH_ANSWER"; then
      open "$APP_LAUNCH_PATH" >/dev/null 2>&1 || true
    fi
  fi
fi
