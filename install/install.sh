#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
REPO_ROOT="${SCRIPT_DIR:h}"
PYTHON_BIN="${PYTHON_BIN:-}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
CODEX_BIN="${CODEX_BIN:-}"
CODEX_BIN_EXPLICIT=0
if [[ -n "$CODEX_BIN" ]]; then
  CODEX_BIN_EXPLICIT=1
fi
CLAUDE_HOME="${CLAUDE_HOME:-$HOME/.claude}"
CLAUDE_BIN="${CLAUDE_BIN:-}"
CLAUDE_MODEL="${OPENRELIX_CLAUDE_MODEL:-${AI_ASSET_CLAUDE_MODEL:-auto}}"
CLAUDE_SETTINGS="${OPENRELIX_CLAUDE_SETTINGS:-${AI_ASSET_CLAUDE_SETTINGS:-}}"
CLAUDE_ENV_FILE="${OPENRELIX_CLAUDE_ENV_FILE:-${AI_ASSET_CLAUDE_ENV_FILE:-}}"
STATE_DIR="${AI_ASSET_STATE_DIR:-}"
LANGUAGE="${AI_ASSET_LANGUAGE:-}"
MEMORY_MODE="${AI_ASSET_MEMORY_MODE:-}"
ACTIVITY_SOURCE="${OPENRELIX_ACTIVITY_SOURCE:-${AI_ASSET_ACTIVITY_SOURCE:-auto}}"
ACTIVITY_HOST="${OPENRELIX_ACTIVITY_HOST:-${AI_ASSET_ACTIVITY_HOST:-all}}"
MODEL_CLI="${OPENRELIX_MODEL_CLI:-${AI_ASSET_MODEL_CLI:-}}"
STATE_DIR_EXPLICIT=0
if [[ -n "${AI_ASSET_STATE_DIR:-}" ]]; then
  STATE_DIR_EXPLICIT=1
fi
CODEX_BIN_AVAILABLE=0
CLAUDE_BIN_AVAILABLE=0

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
OVERVIEW_REFRESH_INTERVAL_MINUTES="${OPENRELIX_OVERVIEW_REFRESH_INTERVAL_MINUTES:-60}"
OVERVIEW_REFRESH_INTERVAL_SECONDS=3600
INSTALL_LEARN_JOBS="${OPENRELIX_INSTALL_LEARN_JOBS:-2}"
INSTALL_DEEP_LEARN_JOBS=1
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
  --claude-home PATH            Override Claude Code data home. Default: ~/.claude
                                This does not override Claude CLI auth/config; use --claude-env-file
                                for explicit CLAUDE_CONFIG_DIR or provider env.
  --claude-bin PATH             Override the Claude Code CLI binary used by model backfill jobs.
  --claude-model MODEL          Claude model or alias for OpenRelix internal claude -p calls.
                                Default: auto, which lets Claude Code choose its configured provider/model.
  --claude-settings PATH|JSON   Extra Claude Code --settings path or JSON for third-party providers,
                                apiKeyHelper, bridge mode, Bedrock/Vertex, etc.
  --claude-env-file PATH        Env file loaded only for OpenRelix claude -p calls.
                                Keep secrets outside the repo and pass the file path here.
  --model-cli CLI               Model CLI for memory backfill: codex | claude | cc.
                                Interactive installs prompt; non-interactive installs default to codex.
  --language CODE               Runtime language: zh | en. Controls panel rendering, local memory
                                storage, and model-generated summaries/next-actions — not just UI strings.
                                If omitted, interactive installs prompt; non-interactive installs default to zh.
  --memory-mode MODE            Memory mode: integrated | local-only | off.
                                Default: integrated.
  --record-memory-only          Record personal memory locally, but disable host context injection.
                                Alias for --memory-mode local-only.
  --use-integrated              Record personal memory and use host context injection.
                                Alias for --memory-mode integrated.
  --disable-personal-memory     Turn off this system's local memory writes.
                                Alias for --memory-mode off.
  --python PATH                 Override the Python binary used by launchd jobs.
  --sync-memory-summary         Explicitly write a bounded summary into enabled host context.
  --no-memory-summary           Skip host memory summary sync and keep context injection off.
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
  --no-learn                    Skip the post-install prompt for two-step memory backfill.
  --install-learn-jobs N        Jobs for post-install shallow backfill. Default: 2, max: 2.
  --enable-background-services  Install overview refresh and token-live LaunchAgents.
  --enable-nightly              Install nightly organize/finalize LaunchAgents.
                                Integrated installs enable nightly by default.
  --enable-update-check         Install a daily no-mutation npm update check LaunchAgent.
  --update-check-time HH:MM     Time for the daily update check. Default: 09:30
  --overview-refresh-interval-minutes N
                                Panel refresh interval for the overview LaunchAgent.
                                Default: 60.
  --enable-learning-refresh     Make the overview refresh call the
                                configured activity host adapter and learn memory with a 7-day
                                window. Implies --enable-background-services.
  --disable-learning-refresh    Keep the overview refresh from learning
                                recent windows. Chinese display polish can still
                                run unless OPENRELIX_ENABLE_NATIVE_DISPLAY_POLISH=0.
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
  --activity-host HOST          Activity host: codex | claude | cc | all.
                                Default: all. Windows keep ai_host in raw payloads.
  --read-codex-app              Alias for --activity-source auto.
                                Kept for compatibility with older install commands.
  -h, --help                    Show this help text.

This __OPENRELIX_VERSION_LABEL__ preview installer currently supports macOS only.
The installer defaults to integrated personal memory: it records into the
configured state root and syncs a bounded summary into enabled host context.
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

INSTALL_CHILD_PID=""

stop_install_child_process() {
  local pid="${INSTALL_CHILD_PID:-}"
  local attempt
  if [[ -z "$pid" ]]; then
    return 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  kill -INT "$pid" 2>/dev/null || true
  for attempt in {1..20}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
    sleep 0.1
  done
  kill -TERM "$pid" 2>/dev/null || true
  for attempt in {1..20}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
    sleep 0.1
  done
  kill -KILL "$pid" 2>/dev/null || true
}

handle_install_signal() {
  local signal_name="$1"
  trap '' HUP INT TERM
  printf '\n%s\n' "$(localized_text "安装已收到中断信号，正在停止子任务..." "Installer received an interrupt; stopping child tasks...")" >&2
  stop_install_child_process
  case "$signal_name" in
    HUP) exit 129 ;;
    INT) exit 130 ;;
    TERM) exit 143 ;;
  esac
  exit 1
}

run_interruptible_child() {
  local child_status=0
  "$@" &
  INSTALL_CHILD_PID=$!
  wait "$INSTALL_CHILD_PID" || child_status=$?
  INSTALL_CHILD_PID=""
  return "$child_status"
}

trap 'handle_install_signal HUP' HUP
trap 'handle_install_signal INT' INT
trap 'handle_install_signal TERM' TERM

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
      ENABLE_NIGHTLY=1
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

select_model_cli() {
  local answer=""
  if [[ -n "$MODEL_CLI" ]]; then
    return
  fi

  if [[ -t 0 && -z "${CI:-}" ]]; then
    print -r -- ""
    print -r -- "Select model CLI for memory backfill / 选择大模型记忆回溯使用的 CLI:"
    print -r -- "  1) Codex CLI - default"
    print -r -- "  2) Claude Code CLI"
    while true; do
      printf "Model CLI [1/2/codex/claude/cc, default codex]: "
      if ! IFS= read -r answer; then
        print -r -- ""
        MODEL_CLI="codex"
        return
      fi
      answer="${answer:l}"
      case "$answer" in
        ""|1|codex|codex-cli|codex_cli)
          MODEL_CLI="codex"
          return
          ;;
        2|claude|cc|claude-code|claude_code|claude-code-cli|claude_code_cli)
          MODEL_CLI="claude"
          return
          ;;
        *)
          print -r -- "Please enter 1/codex or 2/claude."
          ;;
      esac
    done
  fi

  MODEL_CLI="codex"
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
      CODEX_BIN_EXPLICIT=1
      shift 2
      ;;
    --claude-home)
      require_option_value "$1" "${2-}"
      CLAUDE_HOME="$2"
      shift 2
      ;;
    --claude-bin)
      require_option_value "$1" "${2-}"
      CLAUDE_BIN="$2"
      shift 2
      ;;
    --claude-model)
      require_option_value "$1" "${2-}"
      CLAUDE_MODEL="$2"
      shift 2
      ;;
    --claude-model=*)
      CLAUDE_MODEL="${1#*=}"
      shift
      ;;
    --claude-settings)
      require_option_value "$1" "${2-}"
      CLAUDE_SETTINGS="$2"
      shift 2
      ;;
    --claude-settings=*)
      CLAUDE_SETTINGS="${1#*=}"
      shift
      ;;
    --claude-env-file)
      require_option_value "$1" "${2-}"
      CLAUDE_ENV_FILE="$2"
      shift 2
      ;;
    --claude-env-file=*)
      CLAUDE_ENV_FILE="${1#*=}"
      shift
      ;;
    --model-cli)
      require_option_value "$1" "${2-}"
      MODEL_CLI="$2"
      shift 2
      ;;
    --model-cli=*)
      MODEL_CLI="${1#*=}"
      shift
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
    --install-learn-jobs)
      require_option_value "$1" "${2-}"
      INSTALL_LEARN_JOBS="$2"
      shift 2
      ;;
    --install-learn-jobs=*)
      INSTALL_LEARN_JOBS="${1#*=}"
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
    --overview-refresh-interval-minutes)
      require_option_value "$1" "${2-}"
      OVERVIEW_REFRESH_INTERVAL_MINUTES="$2"
      shift 2
      ;;
    --overview-refresh-interval-minutes=*)
      OVERVIEW_REFRESH_INTERVAL_MINUTES="${1#*=}"
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
    --activity-host)
      require_option_value "$1" "${2-}"
      ACTIVITY_HOST="$2"
      shift 2
      ;;
    --activity-host=*)
      ACTIVITY_HOST="${1#*=}"
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
select_model_cli

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

resolve_default_codex_bin() {
  CODEX_BIN="" "$PYTHON_BIN" - "$REPO_ROOT" <<'PY'
import os
import sys

repo_root = sys.argv[1]
sys.path.insert(0, repo_root + "/scripts")

os.environ.pop("CODEX_BIN", None)
from asset_runtime import default_codex_binary  # noqa: E402

print(default_codex_binary())
PY
}

refresh_codex_bin_state() {
  local codex_bin_dir=""
  CODEX_BIN_AVAILABLE=0
  if [[ -n "$CODEX_BIN" && -x "$CODEX_BIN" ]]; then
    CODEX_BIN_AVAILABLE=1
    codex_bin_dir="${CODEX_BIN:h}"
    SAFE_PATH="$codex_bin_dir:$SAFE_PATH"
  fi
}

codex_app_installed() {
  [[ -d "$HOME/Applications/Codex.app" || -d "/Applications/Codex.app" ]]
}

install_codex_cli_now() {
  local npm_bin=""
  npm_bin="$(command -v npm || true)"
  if [[ -z "$npm_bin" ]]; then
    echo "$(localized_text "未找到 npm，无法自动安装 Codex CLI。请先安装 Node.js/npm，再运行：" "npm was not found, so the installer cannot install Codex CLI automatically. Install Node.js/npm first, then run:")" >&2
    echo "  npm install -g @openai/codex@latest" >&2
    return 1
  fi
  "$npm_bin" install -g @openai/codex@latest
}

prompt_install_codex_cli_if_needed() {
  local answer=""
  if [[ "$MODEL_CLI" != "codex" || "$CODEX_BIN_AVAILABLE" == "1" ]]; then
    return 0
  fi
  if (( CODEX_BIN_EXPLICIT )); then
    echo "$(localized_text "指定的 Codex CLI 路径不可执行：" "The specified Codex CLI path is not executable:") $CODEX_BIN" >&2
    echo "$(localized_text "请修正 --codex-bin，或安装最新版 Codex CLI：" "Fix --codex-bin, or install the latest Codex CLI:")" >&2
    echo "  npm install -g @openai/codex@latest" >&2
    exit 1
  fi

  if codex_app_installed; then
    echo "$(localized_text "检测到 Codex App，但没有找到 codex CLI。" "Codex App was detected, but the codex CLI was not found.")" >&2
  else
    echo "$(localized_text "没有找到 codex CLI。" "The codex CLI was not found.")" >&2
  fi
  echo "$(localized_text "Codex App 本身不提供 OpenRelix 记忆回溯需要的命令行能力。" "Codex App itself does not provide the command-line capability OpenRelix needs for memory backfill.")" >&2

  if [[ -t 0 && -z "${CI:-}" ]]; then
    printf "%s " "$(localized_text "是否现在安装最新版 Codex CLI？将运行：npm install -g @openai/codex@latest [Y/n]" "Install the latest Codex CLI now? This will run: npm install -g @openai/codex@latest [Y/n]")" >&2
    if ! IFS= read -r answer; then
      answer="n"
    fi
    answer="${answer:l}"
    case "$answer" in
      ""|y|yes|1|是|好|安装)
        if install_codex_cli_now; then
          CODEX_BIN="$(resolve_default_codex_bin)"
          refresh_codex_bin_state
          if [[ "$CODEX_BIN_AVAILABLE" == "1" ]]; then
            echo "$(localized_text "Codex CLI 已安装：" "Codex CLI installed:") $CODEX_BIN" >&2
            return 0
          fi
        fi
        echo "$(localized_text "Codex CLI 自动安装后仍未找到可执行文件。请确认 npm global bin 在 PATH 中，或通过 --codex-bin 指定路径。" "Codex CLI still could not be found after installation. Make sure npm global bin is on PATH, or pass --codex-bin.")" >&2
        exit 1
        ;;
      *)
        echo "$(localized_text "已跳过自动安装。要使用 Codex 做记忆回溯，请先运行：" "Skipped automatic installation. To use Codex for memory backfill, run:")" >&2
        echo "  npm install -g @openai/codex@latest" >&2
        echo "$(localized_text "也可以改用 Claude Code：重新运行 installer 并选择 --model-cli claude。" "Or use Claude Code instead: rerun the installer with --model-cli claude.")" >&2
        exit 1
        ;;
    esac
  fi

  echo "$(localized_text "非交互环境无法自动确认安装。请先运行：" "This non-interactive environment cannot confirm installation automatically. Run:")" >&2
  echo "  npm install -g @openai/codex@latest" >&2
  echo "$(localized_text "然后重新运行 installer，或通过 --codex-bin /full/path/to/codex 指定路径。" "Then rerun the installer, or pass --codex-bin /full/path/to/codex.")" >&2
  exit 1
}

# Resolve the Codex CLI binary so LaunchAgents (which run with a narrow PATH)
# can still reach app-server when the CLI is installed. Codex App alone does
# not provide the headless CLI used for model backfill.
if [[ -z "$CODEX_BIN" ]]; then
  CODEX_BIN="$(resolve_default_codex_bin)"
fi
SAFE_PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
refresh_codex_bin_state

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

ACTIVITY_HOST="$(
  "$PYTHON_BIN" - "$REPO_ROOT" "$ACTIVITY_HOST" <<'PY'
import sys

repo_root = sys.argv[1]
activity_host = sys.argv[2]
sys.path.insert(0, repo_root + "/scripts")

from asset_runtime import normalize_activity_host  # noqa: E402

try:
    print(normalize_activity_host(activity_host, strict=True))
except ValueError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1)
PY
)"

MODEL_CLI="$(
  "$PYTHON_BIN" - "$REPO_ROOT" "$MODEL_CLI" <<'PY'
import sys

repo_root = sys.argv[1]
model_cli = sys.argv[2]
sys.path.insert(0, repo_root + "/scripts")

from asset_runtime import normalize_model_cli  # noqa: E402

try:
    print(normalize_model_cli(model_cli, strict=True))
except ValueError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1)
PY
)"

CLAUDE_MODEL="$(
  "$PYTHON_BIN" - "$REPO_ROOT" "$CLAUDE_MODEL" <<'PY'
import sys

repo_root = sys.argv[1]
claude_model = sys.argv[2]
sys.path.insert(0, repo_root + "/scripts")

from asset_runtime import normalize_claude_model  # noqa: E402

try:
    print(normalize_claude_model(claude_model, strict=True))
except ValueError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1)
PY
)"

if [[ -z "$CLAUDE_BIN" ]]; then
  CLAUDE_BIN="$(
    CLAUDE_BIN="" "$PYTHON_BIN" - "$REPO_ROOT" <<'PY'
import os
import sys

repo_root = sys.argv[1]
sys.path.insert(0, repo_root + "/scripts")

os.environ.pop("CLAUDE_BIN", None)
from asset_runtime import default_claude_binary  # noqa: E402

print(default_claude_binary())
PY
  )"
fi
CLAUDE_BIN_DIR=""
if [[ -n "$CLAUDE_BIN" && -x "$CLAUDE_BIN" ]]; then
  CLAUDE_BIN_AVAILABLE=1
  CLAUDE_BIN_DIR="${CLAUDE_BIN:h}"
  SAFE_PATH="$CLAUDE_BIN_DIR:$SAFE_PATH"
fi

prompt_install_codex_cli_if_needed
if [[ "$MODEL_CLI" == "claude" && "$CLAUDE_BIN_AVAILABLE" != "1" ]]; then
  echo "Could not locate the Claude Code CLI binary for memory backfill." >&2
  echo "Install Claude Code CLI or pass --claude-bin /full/path/to/claude." >&2
  echo "If you use Codex for backfill, rerun with --model-cli codex after installing Codex CLI." >&2
  exit 1
fi

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
if ! [[ "$OVERVIEW_REFRESH_INTERVAL_MINUTES" =~ '^[0-9]+$' ]]; then
  echo "--overview-refresh-interval-minutes must be a positive integer: $OVERVIEW_REFRESH_INTERVAL_MINUTES" >&2
  exit 1
fi
if (( OVERVIEW_REFRESH_INTERVAL_MINUTES < 1 )); then
  echo "--overview-refresh-interval-minutes must be at least 1 minute: $OVERVIEW_REFRESH_INTERVAL_MINUTES" >&2
  exit 1
fi
OVERVIEW_REFRESH_INTERVAL_SECONDS=$((OVERVIEW_REFRESH_INTERVAL_MINUTES * 60))
if ! [[ "$INSTALL_LEARN_JOBS" =~ '^[0-9]+$' ]]; then
  echo "--install-learn-jobs must be a positive integer: $INSTALL_LEARN_JOBS" >&2
  exit 1
fi
if (( INSTALL_LEARN_JOBS < 1 )); then
  INSTALL_LEARN_JOBS=1
elif (( INSTALL_LEARN_JOBS > 2 )); then
  INSTALL_LEARN_JOBS=2
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
    --set "CLAUDE_BIN=$CLAUDE_BIN" \
    --set "CLAUDE_HOME=$CLAUDE_HOME" \
    --set "SAFE_PATH=$SAFE_PATH" \
    --set "ACTIVITY_SOURCE=$ACTIVITY_SOURCE" \
    --set "ACTIVITY_HOST=$ACTIVITY_HOST" \
    --set "MODEL_CLI=$MODEL_CLI" \
    --set "CLAUDE_MODEL=$CLAUDE_MODEL" \
    --set "CLAUDE_SETTINGS=$CLAUDE_SETTINGS" \
    --set "CLAUDE_ENV_FILE=$CLAUDE_ENV_FILE" \
    --set "LEARNING_REFRESH=$ENABLE_LEARNING_REFRESH" \
    --set "LEARNING_REFRESH_WINDOW_DAYS=$LEARNING_REFRESH_WINDOW_DAYS" \
    --set "OVERVIEW_REFRESH_INTERVAL_SECONDS=$OVERVIEW_REFRESH_INTERVAL_SECONDS" \
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
if (( ENABLE_CODEX_MEMORY_SUMMARY )); then
  mkdir -p "$CLAUDE_HOME"
fi
export AI_ASSET_STATE_DIR="$STATE_DIR"
export CODEX_HOME="$CODEX_HOME"
export CLAUDE_HOME="$CLAUDE_HOME"
export CLAUDE_BIN="$CLAUDE_BIN"
export PYTHON_BIN="$PYTHON_BIN"
export AI_ASSET_LANGUAGE="$LANGUAGE"
export AI_ASSET_MEMORY_MODE="$MEMORY_MODE"
export OPENRELIX_ACTIVITY_SOURCE="$ACTIVITY_SOURCE"
export OPENRELIX_ACTIVITY_HOST="$ACTIVITY_HOST"
export OPENRELIX_MODEL_CLI="$MODEL_CLI"
export OPENRELIX_CLAUDE_MODEL="$CLAUDE_MODEL"
export OPENRELIX_CLAUDE_SETTINGS="$CLAUDE_SETTINGS"
export OPENRELIX_CLAUDE_ENV_FILE="$CLAUDE_ENV_FILE"

initialize_state_root() {
  "$PYTHON_BIN" - "$REPO_ROOT" "$LANGUAGE" "$MEMORY_MODE" "$ACTIVITY_SOURCE" "$ACTIVITY_HOST" "$MODEL_CLI" "$CLAUDE_MODEL" "$CLAUDE_SETTINGS" "$CLAUDE_ENV_FILE" <<'PY'
import sys

repo_root = sys.argv[1]
language = sys.argv[2]
memory_mode = sys.argv[3]
activity_source = sys.argv[4]
activity_host = sys.argv[5]
model_cli = sys.argv[6]
claude_model = sys.argv[7]
claude_settings = sys.argv[8]
claude_env_file = sys.argv[9]
sys.path.insert(0, repo_root + "/scripts")

from asset_runtime import ensure_state_layout, write_runtime_config  # noqa: E402

paths = ensure_state_layout()
write_runtime_config(
    language=language,
    memory_mode=memory_mode,
    activity_source=activity_source,
    activity_host=activity_host,
    model_cli=model_cli,
    claude_model=claude_model,
    claude_settings=claude_settings,
    claude_env_file=claude_env_file,
    paths=paths,
)
PY
  if ! "$PYTHON_BIN" "$REPO_ROOT/scripts/collect_codex_activity.py" --stage manual --activity-host "$ACTIVITY_HOST"; then
    echo "openrelix install: initial activity collection failed; the panel will populate after the first refresh." >&2
  fi
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

if (( ENABLE_CODEX_MEMORY_SUMMARY )); then
  run_step "$(localized_text "同步受控的 host 记忆摘要..." "Syncing the bounded host memory summary...")" \
    "$PYTHON_BIN" "$REPO_ROOT/scripts/sync_host_memory_summary.py"
elif [[ "$MEMORY_MODE" != "integrated" ]]; then
  run_step "$(localized_text "清理受控的 host 记忆摘要..." "Clearing managed host memory summaries...")" \
    "$PYTHON_BIN" "$REPO_ROOT/scripts/sync_host_memory_summary.py"
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
    --set "CLAUDE_HOME=$CLAUDE_HOME" \
    --set "CLAUDE_BIN=$CLAUDE_BIN" \
    --set "PYTHON_BIN=$PYTHON_BIN" \
    --set "ACTIVITY_SOURCE=$ACTIVITY_SOURCE" \
    --set "ACTIVITY_HOST=$ACTIVITY_HOST" \
    --set "MODEL_CLI=$MODEL_CLI" \
    --set "CLAUDE_MODEL=$CLAUDE_MODEL" \
    --set "CLAUDE_SETTINGS=$CLAUDE_SETTINGS" \
    --set "CLAUDE_ENV_FILE=$CLAUDE_ENV_FILE"
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
  if (( LEARNING_REFRESH_WINDOW_DAYS == 0 )); then
    if (( INSTALL_GLOBAL_COMMAND )); then
      printf 'openrelix review --stage preliminary --learn-window-days 0 --jobs %s\n' "$INSTALL_LEARN_JOBS"
      return
    fi
    printf 'AI_ASSET_STATE_DIR=%q CODEX_HOME=%q CLAUDE_HOME=%q CLAUDE_BIN=%q AI_ASSET_LANGUAGE=%q OPENRELIX_ACTIVITY_SOURCE=%q OPENRELIX_ACTIVITY_HOST=%q OPENRELIX_MODEL_CLI=%q OPENRELIX_CLAUDE_MODEL=%q OPENRELIX_CLAUDE_SETTINGS=%q OPENRELIX_CLAUDE_ENV_FILE=%q %q %q review --stage preliminary --learn-window-days 0 --jobs %s\n' \
      "$STATE_DIR" \
      "$CODEX_HOME" \
      "$CLAUDE_HOME" \
      "$CLAUDE_BIN" \
      "$LANGUAGE" \
      "$ACTIVITY_SOURCE" \
      "$ACTIVITY_HOST" \
      "$MODEL_CLI" \
      "$CLAUDE_MODEL" \
      "$CLAUDE_SETTINGS" \
      "$CLAUDE_ENV_FILE" \
      "$PYTHON_BIN" \
      "$REPO_ROOT/scripts/openrelix.py" \
      "$INSTALL_LEARN_JOBS"
    return
  fi
  if (( INSTALL_GLOBAL_COMMAND )); then
    printf 'openrelix backfill --days %s --stage preliminary --learn-window-days 0 --jobs %s\n' "$LEARNING_REFRESH_WINDOW_DAYS" "$INSTALL_LEARN_JOBS"
    return
  fi
  printf 'AI_ASSET_STATE_DIR=%q CODEX_HOME=%q CLAUDE_HOME=%q CLAUDE_BIN=%q AI_ASSET_LANGUAGE=%q OPENRELIX_ACTIVITY_SOURCE=%q OPENRELIX_ACTIVITY_HOST=%q OPENRELIX_MODEL_CLI=%q OPENRELIX_CLAUDE_MODEL=%q OPENRELIX_CLAUDE_SETTINGS=%q OPENRELIX_CLAUDE_ENV_FILE=%q %q %q backfill --days %s --stage preliminary --learn-window-days 0 --jobs %s\n' \
    "$STATE_DIR" \
    "$CODEX_HOME" \
    "$CLAUDE_HOME" \
    "$CLAUDE_BIN" \
    "$LANGUAGE" \
    "$ACTIVITY_SOURCE" \
    "$ACTIVITY_HOST" \
    "$MODEL_CLI" \
    "$CLAUDE_MODEL" \
    "$CLAUDE_SETTINGS" \
    "$CLAUDE_ENV_FILE" \
    "$PYTHON_BIN" \
    "$REPO_ROOT/scripts/openrelix.py" \
    "$LEARNING_REFRESH_WINDOW_DAYS" \
    "$INSTALL_LEARN_JOBS"
}

deep_learn_memory_command() {
  if (( LEARNING_REFRESH_WINDOW_DAYS == 0 )); then
    return
  fi
  if (( INSTALL_GLOBAL_COMMAND )); then
    printf 'openrelix backfill --days %s --stage final --learn-window-days %s --jobs %s --force\n' "$LEARNING_REFRESH_WINDOW_DAYS" "$LEARNING_REFRESH_WINDOW_DAYS" "$INSTALL_DEEP_LEARN_JOBS"
    return
  fi
  printf 'AI_ASSET_STATE_DIR=%q CODEX_HOME=%q CLAUDE_HOME=%q CLAUDE_BIN=%q AI_ASSET_LANGUAGE=%q OPENRELIX_ACTIVITY_SOURCE=%q OPENRELIX_ACTIVITY_HOST=%q OPENRELIX_MODEL_CLI=%q OPENRELIX_CLAUDE_MODEL=%q OPENRELIX_CLAUDE_SETTINGS=%q OPENRELIX_CLAUDE_ENV_FILE=%q %q %q backfill --days %s --stage final --learn-window-days %s --jobs %s --force\n' \
    "$STATE_DIR" \
    "$CODEX_HOME" \
    "$CLAUDE_HOME" \
    "$CLAUDE_BIN" \
    "$LANGUAGE" \
    "$ACTIVITY_SOURCE" \
    "$ACTIVITY_HOST" \
    "$MODEL_CLI" \
    "$CLAUDE_MODEL" \
    "$CLAUDE_SETTINGS" \
    "$CLAUDE_ENV_FILE" \
    "$PYTHON_BIN" \
    "$REPO_ROOT/scripts/openrelix.py" \
    "$LEARNING_REFRESH_WINDOW_DAYS" \
    "$LEARNING_REFRESH_WINDOW_DAYS" \
    "$INSTALL_DEEP_LEARN_JOBS"
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

ensure_memory_migration_marker() {
  AI_ASSET_STATE_DIR="$STATE_DIR" \
    CODEX_HOME="$CODEX_HOME" \
    CLAUDE_HOME="$CLAUDE_HOME" \
    CLAUDE_BIN="$CLAUDE_BIN" \
    AI_ASSET_LANGUAGE="$LANGUAGE" \
    AI_ASSET_MEMORY_MODE="$MEMORY_MODE" \
    OPENRELIX_ACTIVITY_SOURCE="$ACTIVITY_SOURCE" \
    OPENRELIX_ACTIVITY_HOST="$ACTIVITY_HOST" \
    OPENRELIX_MODEL_CLI="$MODEL_CLI" \
    OPENRELIX_CLAUDE_MODEL="$CLAUDE_MODEL" \
    OPENRELIX_CLAUDE_SETTINGS="$CLAUDE_SETTINGS" \
    OPENRELIX_CLAUDE_ENV_FILE="$CLAUDE_ENV_FILE" \
    "$PYTHON_BIN" "$REPO_ROOT/scripts/openrelix.py" memory-migration ensure --quiet || \
    echo "$(localized_text "个人记忆迁移标记写入失败；后续可运行 openrelix memory-migration ensure。" "Personal memory migration marker failed; run openrelix memory-migration ensure later.")" >&2
}

mark_memory_migration_completed_after_learning() {
  AI_ASSET_STATE_DIR="$STATE_DIR" \
    CODEX_HOME="$CODEX_HOME" \
    CLAUDE_HOME="$CLAUDE_HOME" \
    CLAUDE_BIN="$CLAUDE_BIN" \
    AI_ASSET_LANGUAGE="$LANGUAGE" \
    AI_ASSET_MEMORY_MODE="$MEMORY_MODE" \
    OPENRELIX_ACTIVITY_SOURCE="$ACTIVITY_SOURCE" \
    OPENRELIX_ACTIVITY_HOST="$ACTIVITY_HOST" \
    OPENRELIX_MODEL_CLI="$MODEL_CLI" \
    OPENRELIX_CLAUDE_MODEL="$CLAUDE_MODEL" \
    OPENRELIX_CLAUDE_SETTINGS="$CLAUDE_SETTINGS" \
    OPENRELIX_CLAUDE_ENV_FILE="$CLAUDE_ENV_FILE" \
    "$PYTHON_BIN" "$REPO_ROOT/scripts/openrelix.py" memory-migration complete --window-days "$LEARNING_REFRESH_WINDOW_DAYS" --quiet || \
    echo "$(localized_text "个人记忆迁移完成标记写入失败；后续可运行 openrelix memory-migration complete。" "Personal memory migration completion marker failed; run openrelix memory-migration complete later.")" >&2
}

is_ci_environment() {
  [[ -n "${CI:-}" && "${CI:-}" != "0" && "${CI:-}" != "false" ]] || \
    [[ -n "${OPENRELIX_NO_LAUNCH:-}" && "${OPENRELIX_NO_LAUNCH:-}" != "0" && "${OPENRELIX_NO_LAUNCH:-}" != "false" ]]
}

LEARN_MEMORY_COMMAND="$(learn_memory_command)"
DEEP_LEARN_MEMORY_COMMAND="$(deep_learn_memory_command)"
OPEN_PANEL_COMMAND="$(open_panel_command)"
MAC_APP_COMMAND="$(mac_app_command)"
WEB_PANEL_COMMAND="$(web_panel_command)"
WILL_AUTO_LAUNCH=0
if [[ "$OSTYPE" == darwin* ]] && (( MAC_CLIENT_INSTALLED )) && (( LAUNCH_AFTER_INSTALL )) && ! is_ci_environment; then
  WILL_AUTO_LAUNCH=1
fi
if (( LEARNING_REFRESH_WINDOW_DAYS == 0 )); then
  if [[ "$MEMORY_MODE" == "integrated" ]]; then
    REVIEW_CONTEXT_NOTE_ZH="这一步只读取并轻量整理当天窗口，写入可复用压缩层，不做历史学习。当前 integrated 会把同一份 bounded summary 同步到启用的 host context，但不会把原始窗口写进原生 memory。"
    REVIEW_CONTEXT_NOTE_EN="This only reads and lightly organizes today's window, stores a reusable compact layer, and does not run historical learning. The current integrated mode syncs the same bounded summary into enabled host contexts, but does not write raw windows into native memory."
  else
    REVIEW_CONTEXT_NOTE_ZH="这一步只读取并轻量整理当天窗口，写入可复用压缩层，不做历史学习。当前 $MEMORY_MODE 不会向 host context 同步摘要。"
    REVIEW_CONTEXT_NOTE_EN="This only reads and lightly organizes today's window, stores a reusable compact layer, and does not run historical learning. The current $MEMORY_MODE mode does not sync a summary into host context."
  fi
elif [[ "$MEMORY_MODE" == "integrated" ]]; then
  REVIEW_CONTEXT_NOTE_ZH="浅度回溯会读取并轻量整理最近 ${LEARNING_REFRESH_WINDOW_DAYS} 天窗口，写入可复用压缩层；随后 final 深度学习会复用这层结果。当前 integrated 会把同一份 bounded summary 同步到启用的 host context，但不会把原始窗口写进原生 memory。"
  REVIEW_CONTEXT_NOTE_EN="The shallow backfill reads and lightly organizes the last ${LEARNING_REFRESH_WINDOW_DAYS} days of windows, then stores a reusable compact layer for later final consolidation. The current integrated mode syncs the same bounded summary into enabled host contexts, but does not write raw windows into native memory."
else
  REVIEW_CONTEXT_NOTE_ZH="浅度回溯会读取并轻量整理最近 ${LEARNING_REFRESH_WINDOW_DAYS} 天窗口，写入可复用压缩层；随后 final 深度学习会复用这层结果。当前 $MEMORY_MODE 不会向 host context 同步摘要。"
  REVIEW_CONTEXT_NOTE_EN="The shallow backfill reads and lightly organizes the last ${LEARNING_REFRESH_WINDOW_DAYS} days of windows, then stores a reusable compact layer for later final consolidation. The current $MEMORY_MODE mode does not sync a summary into host context."
fi

ensure_memory_migration_marker

if [[ "$LANGUAGE" == "zh" ]]; then
  cat <<EOF
OpenRelix 已安装完成。

安装信息：
  安装模式: $INSTALL_PROFILE
  源码目录: $REPO_ROOT
  状态目录: $STATE_DIR
  Codex 目录: $CODEX_HOME
  Claude Code 目录: $CLAUDE_HOME
  语言: $LANGUAGE
  记忆模式: $MEMORY_MODE
  活动来源: $ACTIVITY_SOURCE
  活动 host: $ACTIVITY_HOST
  记忆回溯 CLI: $MODEL_CLI
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
  2. 已开启 1 小时自动学习刷新；首次自动学习会在下一个 1 小时周期运行。
     当前窗口 host: $ACTIVITY_HOST；模型回溯 CLI: $MODEL_CLI。Codex 窗口默认会先尝试 app-server，失败时回退 CLI history/session；如需只读稳定 CLI 文件，安装时加 --activity-source history。
EOF
  else
    cat <<EOF
  2. 推荐：安装后先浅度回溯最近 ${LEARNING_REFRESH_WINDOW_DAYS} 天窗口，生成快速摘要和索引：
     $LEARN_MEMORY_COMMAND
EOF
    if [[ -n "$DEEP_LEARN_MEMORY_COMMAND" ]]; then
      cat <<EOF
     记忆沉淀会在 deep/final 回溯中完成，可随后运行：
     $DEEP_LEARN_MEMORY_COMMAND
EOF
    fi
    cat <<EOF
     $REVIEW_CONTEXT_NOTE_ZH
     当前窗口 host: $ACTIVITY_HOST；模型回溯 CLI: $MODEL_CLI。Codex 窗口默认会先尝试 app-server，失败时回退 CLI history/session；如需只读稳定 CLI 文件，安装时加 --activity-source history。
EOF
  fi

  if (( INSTALL_GLOBAL_SKILLS )); then
    cat <<EOF
  3. 在新的 Codex 线程里，需要临时复盘任务时可以直接输入 memory-review（不要带斜杠，避免 CLI 当成未知 slash command 拦截）。
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
  overview-refresh 已安装为每 ${OVERVIEW_REFRESH_INTERVAL_MINUTES} 分钟自动学习刷新一次，会读取当前 activity host，并用 $MODEL_CLI 回溯最近 ${LEARNING_REFRESH_WINDOW_DAYS} 天窗口。
EOF
    else
      cat <<EOF

后台刷新：
  overview-refresh 已安装为每 ${OVERVIEW_REFRESH_INTERVAL_MINUTES} 分钟刷新一次；当前不会从最近窗口自动学习。中文展示润色仍会按需维护缓存；如需自动学习刷新，重新安装时加 --enable-learning-refresh。
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
  Claude Code home: $CLAUDE_HOME
  Language: $LANGUAGE
  Memory mode: $MEMORY_MODE
  Activity source: $ACTIVITY_SOURCE
  Activity host: $ACTIVITY_HOST
  Memory backfill CLI: $MODEL_CLI
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
  2. Automatic learning refresh is enabled; the first learning run will happen on the next configured interval.
     Current activity host: $ACTIVITY_HOST; model backfill CLI: $MODEL_CLI. Codex windows try app-server first and fall back to CLI history/session; add --activity-source history to force stable CLI files only.
EOF
  else
    cat <<EOF
  2. Recommended: first run a shallow backfill for the last ${LEARNING_REFRESH_WINDOW_DAYS} days and refresh local memory:
     $LEARN_MEMORY_COMMAND
EOF
    if [[ -n "$DEEP_LEARN_MEMORY_COMMAND" ]]; then
      cat <<EOF
     Deep backfill for the last ${LEARNING_REFRESH_WINDOW_DAYS} days can then run:
     $DEEP_LEARN_MEMORY_COMMAND
EOF
    fi
    cat <<EOF
     $REVIEW_CONTEXT_NOTE_EN
     Current activity host: $ACTIVITY_HOST; model backfill CLI: $MODEL_CLI. Codex windows try app-server first and fall back to CLI history/session; add --activity-source history to force stable CLI files only.
EOF
  fi

  if (( INSTALL_GLOBAL_SKILLS )); then
    cat <<EOF
  3. In a new Codex thread, type memory-review without a slash when you need an immediate task review so Codex CLI does not intercept it as an unknown slash command.
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
  overview-refresh is installed to learn automatically every ${OVERVIEW_REFRESH_INTERVAL_MINUTES} minutes. It reads the current activity host and uses $MODEL_CLI for the last ${LEARNING_REFRESH_WINDOW_DAYS} days of memory backfill.
EOF
    else
      cat <<EOF

Background refresh:
  overview-refresh is installed to refresh every ${OVERVIEW_REFRESH_INTERVAL_MINUTES} minutes without learning from recent windows. Chinese display polish still maintains its cache as needed. Reinstall with --enable-learning-refresh for automatic learning refresh.
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

run_post_install_shallow_learning() {
  if (( LEARNING_REFRESH_WINDOW_DAYS == 0 )); then
    AI_ASSET_STATE_DIR="$STATE_DIR" \
      CODEX_HOME="$CODEX_HOME" \
      CLAUDE_HOME="$CLAUDE_HOME" \
      CLAUDE_BIN="$CLAUDE_BIN" \
      AI_ASSET_LANGUAGE="$LANGUAGE" \
      OPENRELIX_ACTIVITY_SOURCE="$ACTIVITY_SOURCE" \
      OPENRELIX_ACTIVITY_HOST="$ACTIVITY_HOST" \
      OPENRELIX_MODEL_CLI="$MODEL_CLI" \
      OPENRELIX_CLAUDE_MODEL="$CLAUDE_MODEL" \
      OPENRELIX_CLAUDE_SETTINGS="$CLAUDE_SETTINGS" \
      OPENRELIX_CLAUDE_ENV_FILE="$CLAUDE_ENV_FILE" \
      run_interruptible_child "$PYTHON_BIN" "$REPO_ROOT/scripts/openrelix.py" \
      review --stage preliminary --learn-window-days 0 --jobs "$INSTALL_LEARN_JOBS"
    return
  fi
  AI_ASSET_STATE_DIR="$STATE_DIR" \
    CODEX_HOME="$CODEX_HOME" \
    CLAUDE_HOME="$CLAUDE_HOME" \
    CLAUDE_BIN="$CLAUDE_BIN" \
    AI_ASSET_LANGUAGE="$LANGUAGE" \
    OPENRELIX_ACTIVITY_SOURCE="$ACTIVITY_SOURCE" \
    OPENRELIX_ACTIVITY_HOST="$ACTIVITY_HOST" \
    OPENRELIX_MODEL_CLI="$MODEL_CLI" \
    OPENRELIX_CLAUDE_MODEL="$CLAUDE_MODEL" \
    OPENRELIX_CLAUDE_SETTINGS="$CLAUDE_SETTINGS" \
    OPENRELIX_CLAUDE_ENV_FILE="$CLAUDE_ENV_FILE" \
    run_interruptible_child "$PYTHON_BIN" "$REPO_ROOT/scripts/openrelix.py" \
    backfill --days "$LEARNING_REFRESH_WINDOW_DAYS" --stage preliminary --learn-window-days 0 --jobs "$INSTALL_LEARN_JOBS"
}

print_post_install_shallow_ready() {
  if [[ "$LANGUAGE" == "en" ]]; then
    print -r -- ""
    if (( LEARNING_REFRESH_WINDOW_DAYS == 0 )); then
      print -r -- "Lightweight backfill is complete. OpenRelix is ready to use now; if the browser panel or app is already open, refresh it to see the quick summary."
    else
      print -r -- "Lightweight backfill is complete. OpenRelix is ready to use now; if the browser panel or app is already open, refresh it to see the quick summary. Deep backfill will continue in this terminal."
    fi
  else
    print -r -- ""
    if (( LEARNING_REFRESH_WINDOW_DAYS == 0 )); then
      print -r -- "浅度回溯已完成，OpenRelix 现在可以先使用了；如果浏览器面板或 app 已经打开，手动刷新即可看到快速总结。"
    else
      print -r -- "浅度回溯已完成，OpenRelix 现在可以先使用了；如果浏览器面板或 app 已经打开，手动刷新即可看到快速总结。接下来会在当前终端继续深度回溯。"
    fi
  fi
}

print_post_install_shallow_failed() {
  if [[ "$LANGUAGE" == "en" ]]; then
    print -r -- ""
    if (( LEARNING_REFRESH_WINDOW_DAYS == 0 )); then
      print -r -- "Lightweight backfill did not complete cleanly. OpenRelix will still open."
    else
      print -r -- "Lightweight backfill did not complete cleanly. OpenRelix will still open, and deep backfill will continue in this terminal."
    fi
  else
    print -r -- ""
    if (( LEARNING_REFRESH_WINDOW_DAYS == 0 )); then
      print -r -- "浅度回溯未完整完成。OpenRelix 仍会打开。"
    else
      print -r -- "浅度回溯未完整完成。OpenRelix 仍会打开，并继续在当前终端尝试深度回溯。"
    fi
  fi
}

launch_app_after_shallow_learning() {
  if [[ "$OSTYPE" == darwin* ]] && (( WILL_AUTO_LAUNCH )) && [[ -d "$INSTALLED_MAC_CLIENT_APP" ]]; then
    open "$INSTALLED_MAC_CLIENT_APP" >/dev/null 2>&1 || true
    WILL_AUTO_LAUNCH=0
  fi
}

run_post_install_deep_learning() {
  if (( LEARNING_REFRESH_WINDOW_DAYS == 0 )); then
    return
  fi
  if [[ "$LANGUAGE" == "en" ]]; then
    print -r -- ""
    print -r -- "Starting serial deep learning backfill for the last ${LEARNING_REFRESH_WINDOW_DAYS} days. Progress will stay visible in this terminal."
  else
    print -r -- ""
    print -r -- "开始串行深度回溯最近 ${LEARNING_REFRESH_WINDOW_DAYS} 天，进度会继续显示在当前终端。"
  fi
  AI_ASSET_STATE_DIR="$STATE_DIR" \
    CODEX_HOME="$CODEX_HOME" \
    CLAUDE_HOME="$CLAUDE_HOME" \
    CLAUDE_BIN="$CLAUDE_BIN" \
    AI_ASSET_LANGUAGE="$LANGUAGE" \
    OPENRELIX_ACTIVITY_SOURCE="$ACTIVITY_SOURCE" \
    OPENRELIX_ACTIVITY_HOST="$ACTIVITY_HOST" \
    OPENRELIX_MODEL_CLI="$MODEL_CLI" \
    OPENRELIX_CLAUDE_MODEL="$CLAUDE_MODEL" \
    OPENRELIX_CLAUDE_SETTINGS="$CLAUDE_SETTINGS" \
    OPENRELIX_CLAUDE_ENV_FILE="$CLAUDE_ENV_FILE" \
  run_interruptible_child "$PYTHON_BIN" "$REPO_ROOT/scripts/openrelix.py" \
    backfill --days "$LEARNING_REFRESH_WINDOW_DAYS" --stage final --learn-window-days "$LEARNING_REFRESH_WINDOW_DAYS" --jobs "$INSTALL_DEEP_LEARN_JOBS" --force
  mark_memory_migration_completed_after_learning
  if [[ "$LANGUAGE" == "en" ]]; then
    print -r -- ""
    print -r -- "Deep learning backfill is complete. If the browser panel or OpenRelix app is already open, refresh it manually to see the final memories and summaries."
  else
    print -r -- ""
    print -r -- "深度回溯已完成。如果浏览器面板或 OpenRelix app 已经打开，请手动刷新当前页面或 app，查看终版记忆和日报。"
  fi
}

if (( INTERACTIVE_TTY )) && (( LEARN_AFTER_INSTALL )); then
  if [[ "$LANGUAGE" == "en" ]]; then
    print -r -- ""
    print -r -- "Run the default two-step memory backfill now?"
    if (( LEARNING_REFRESH_WINDOW_DAYS == 0 )); then
      print -r -- "This stores a fast reusable compact layer for today without historical learning."
    elif (( WILL_AUTO_LAUNCH )); then
      print -r -- "Step 1 stores a reusable lightweight layer; the app opens next, then Step 2 serially backfills the last ${LEARNING_REFRESH_WINDOW_DAYS} days deeply in this terminal."
    else
      print -r -- "Step 1 stores a reusable lightweight layer; Step 2 serially backfills the last ${LEARNING_REFRESH_WINDOW_DAYS} days deeply in this terminal after Step 1."
    fi
    print -r -- "  $LEARN_MEMORY_COMMAND"
    if (( WILL_AUTO_LAUNCH )); then
      print -r -- "  $MAC_APP_COMMAND"
    fi
    if [[ -n "$DEEP_LEARN_MEMORY_COMMAND" ]]; then
      print -r -- "  $DEEP_LEARN_MEMORY_COMMAND"
    fi
    printf "Run it now? [Y/n]: "
  else
    print -r -- ""
    print -r -- "现在执行默认两段式记忆回溯吗？"
    if (( LEARNING_REFRESH_WINDOW_DAYS == 0 )); then
      print -r -- "这会为当天窗口写入可复用轻量层，不做历史学习。"
    elif (( WILL_AUTO_LAUNCH )); then
      print -r -- "第一步先写入可复用轻量层；随后打开 app，并在当前终端串行深度回溯最近 ${LEARNING_REFRESH_WINDOW_DAYS} 天。"
    else
      print -r -- "第一步先写入可复用轻量层；完成后会在当前终端串行深度回溯最近 ${LEARNING_REFRESH_WINDOW_DAYS} 天。"
    fi
    print -r -- "  $LEARN_MEMORY_COMMAND"
    if (( WILL_AUTO_LAUNCH )); then
      print -r -- "  $MAC_APP_COMMAND"
    fi
    if [[ -n "$DEEP_LEARN_MEMORY_COMMAND" ]]; then
      print -r -- "  $DEEP_LEARN_MEMORY_COMMAND"
    fi
    printf "是否执行？[Y/n]: "
  fi
  LEARN_ANSWER=""
  IFS= read -r LEARN_ANSWER || LEARN_ANSWER=""
  if ! is_no_answer "$LEARN_ANSWER"; then
    if run_post_install_shallow_learning; then
      print_post_install_shallow_ready
    else
      print_post_install_shallow_failed
    fi
    launch_app_after_shallow_learning
    run_post_install_deep_learning
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
