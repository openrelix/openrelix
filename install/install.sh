#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
REPO_ROOT="${SCRIPT_DIR:h}"
PYTHON_BIN="${PYTHON_BIN:-}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
STATE_DIR="${AI_ASSET_STATE_DIR:-}"
LANGUAGE="${AI_ASSET_LANGUAGE:-}"
MEMORY_MODE="${AI_ASSET_MEMORY_MODE:-}"
ACTIVITY_SOURCE="${OKEEP_ACTIVITY_SOURCE:-${AI_ASSET_ACTIVITY_SOURCE:-history}}"
STATE_DIR_EXPLICIT=0
if [[ -n "${AI_ASSET_STATE_DIR:-}" ]]; then
  STATE_DIR_EXPLICIT=1
fi

INSTALL_PROFILE="minimal"
INSTALL_GLOBAL_SKILLS=0
INSTALL_CUSTOM_PROMPTS=0
INSTALL_GLOBAL_COMMAND=0
ENABLE_CODEX_MEMORY_SUMMARY=0
ENABLE_MEMORIES=0
DISABLE_CODEX_MEMORIES=0
ENABLE_HISTORY=0
CODEX_MEMORY_SUMMARY_EXPLICIT=0
CODEX_MEMORIES_EXPLICIT=0
CODEX_HISTORY_EXPLICIT=0
ENABLE_BACKGROUND_SERVICES=0
ENABLE_NIGHTLY=0
MEMORY_MODE_EXPLICIT=0
KEEP_AWAKE="none"
BIN_DIR="${AI_ASSET_BIN_DIR:-}"
SHELL_RC_PATH=""
PATH_EXPORT_ADDED=0
STEP_INDEX=0
TOTAL_STEPS=1

usage() {
  cat <<'EOF'
Usage:
  ./install/install.sh [options]

Options:
  --profile MODE                Install profile: minimal | integrated. Default: minimal
  --minimal                     Alias for --profile minimal.
  --integrated                  Alias for --profile integrated.
  --state-dir PATH              Override the runtime state root.
  --codex-home PATH             Override CODEX_HOME. Default: ~/.codex
  --language CODE               Runtime language: zh | en.
                                If omitted, interactive installs prompt; non-interactive installs default to zh.
  --memory-mode MODE            Memory mode: local-only | codex-context | off.
                                Default: codex-context.
  --record-memory-only          Record personal memory locally, but disable Codex native context injection.
                                Alias for --memory-mode local-only.
  --use-codex-context           Record personal memory and fully use Codex native memory context.
                                Alias for --memory-mode codex-context.
  --disable-personal-memory     Turn off this system's local memory writes.
                                Alias for --memory-mode off.
  --python PATH                 Override the Python binary used by launchd jobs.
  --sync-memory-summary         Explicitly write a bounded summary into CODEX_HOME.
  --no-memory-summary           Skip Codex memory summary sync and keep context injection off.
  --install-global-skills       Symlink the memory-review skill into the user Codex skill root.
  --no-global-skills            Skip global skill symlinks.
  --install-custom-prompts      Install repo-provided Codex custom prompts.
  --no-custom-prompts           Skip Codex custom prompt installation.
  --install-global-command      Install the global `okeep` command.
  --no-global-command           Skip global `okeep` command installation.
  --bin-dir PATH                Override the install location for the `okeep` command.
  --enable-background-services  Install overview refresh and token-live LaunchAgents.
  --enable-nightly              Install nightly organize/finalize LaunchAgents.
  --disable-background-services Skip overview refresh and token-live LaunchAgents.
  --keep-awake MODE             Sleep policy for nightly jobs: none | during-job
  --enable-memories             Enable Codex memories config.
  --disable-memories            Do not touch Codex memories config.
  --enable-history              Enable bounded Codex history config.
  --disable-history             Do not touch Codex history config.
  --activity-source SOURCE      Activity source: history | app-server | auto.
                                Default: history.
                                Use app-server or auto only when you explicitly want
                                to read Codex app/server threads.
  --read-codex-app              Alias for --activity-source auto.
                                This opt-in tries Codex app-server first and falls
                                back to history/session files if unavailable.
  -h, --help                    Show this help text.

This v0.1.0 preview installer currently supports macOS only.
The installer defaults to codex-context personal memory: it records into the
configured state root and syncs a bounded summary into Codex native context.
Use --record-memory-only when you explicitly want strict local-only recording
without context injection.
EOF
}

step() {
  STEP_INDEX=$((STEP_INDEX + 1))
  printf '[%d/%d] %s\n' "$STEP_INDEX" "$TOTAL_STEPS" "$1"
}

step_done() {
  printf '        done\n'
}

step_skip() {
  printf '        skipped\n'
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
    --use-codex-context)
      MEMORY_MODE="codex-context"
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
    --enable-background-services)
      ENABLE_BACKGROUND_SERVICES=1
      shift
      ;;
    --enable-nightly)
      ENABLE_NIGHTLY=1
      shift
      ;;
    --disable-background-services)
      ENABLE_BACKGROUND_SERVICES=0
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
  echo "OpenKeepsake v0.1.0 preview installer currently supports macOS only." >&2
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

if (( CODEX_MEMORY_SUMMARY_EXPLICIT )) && (( ! ENABLE_CODEX_MEMORY_SUMMARY )) && [[ "$MEMORY_MODE" == "codex-context" ]]; then
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
  codex-context)
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
if [[ "$OSTYPE" == darwin* ]] && (( ENABLE_BACKGROUND_SERVICES )); then
  TOTAL_STEPS=$((TOTAL_STEPS + 1))
fi
if [[ "$OSTYPE" == darwin* ]] && (( ENABLE_NIGHTLY )); then
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
    --set "CODEX_HOME=$CODEX_HOME" \
    --set "ACTIVITY_SOURCE=$ACTIVITY_SOURCE" \
    --set "KEEP_AWAKE=$KEEP_AWAKE"
}

bootstrap_launch_agent() {
  local plist_path="$1"
  local label="$2"
  local legacy_prefix=""
  local legacy_label=""
  local legacy_plist=""
  for legacy_prefix in io.github.ai-personal-assets io.github.codex-personal-assets; do
    legacy_label="${label/io.github.openkeepsake/$legacy_prefix}"
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
  launchctl kickstart -k "gui/$(id -u)/$label" >/dev/null 2>&1 || true
}

if (( ENABLE_CODEX_MEMORY_SUMMARY || DISABLE_CODEX_MEMORIES || ENABLE_MEMORIES || ENABLE_HISTORY || INSTALL_GLOBAL_SKILLS || INSTALL_CUSTOM_PROMPTS )); then
  mkdir -p "$CODEX_HOME"
fi
export AI_ASSET_STATE_DIR="$STATE_DIR"
export CODEX_HOME="$CODEX_HOME"
export PYTHON_BIN="$PYTHON_BIN"
export AI_ASSET_LANGUAGE="$LANGUAGE"
export AI_ASSET_MEMORY_MODE="$MEMORY_MODE"
export OKEEP_ACTIVITY_SOURCE="$ACTIVITY_SOURCE"

initialize_state_root() {
  "$PYTHON_BIN" - "$REPO_ROOT" "$LANGUAGE" "$MEMORY_MODE" <<'PY'
import sys

repo_root = sys.argv[1]
language = sys.argv[2]
memory_mode = sys.argv[3]
sys.path.insert(0, repo_root + "/scripts")

from asset_runtime import ensure_state_layout, write_runtime_config  # noqa: E402

paths = ensure_state_layout()
write_runtime_config(language=language, memory_mode=memory_mode, paths=paths)
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

run_step "Initializing state root, language config, and first overview..." \
  initialize_state_root

if (( ENABLE_CODEX_MEMORY_SUMMARY )); then
  run_step "Syncing the bounded Codex memory summary..." \
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
  run_step "Configuring Codex user settings..." \
    "$PYTHON_BIN" "$REPO_ROOT/install/configure_codex_user.py" \
    --config "$CODEX_HOME/config.toml" \
    "${config_args[@]}"
fi

if (( INSTALL_GLOBAL_SKILLS )); then
  step "Linking memory-review into the user Codex skill directory..."
  mkdir -p "$CODEX_HOME/skills"
  ln -sfn "$REPO_ROOT/.agents/skills/memory-review" \
    "$CODEX_HOME/skills/memory-review"
  step_done
fi

if (( INSTALL_CUSTOM_PROMPTS )); then
  step "Installing Codex custom prompts..."
  mkdir -p "$CODEX_HOME/prompts"
  "$PYTHON_BIN" "$REPO_ROOT/install/render_template.py" \
    --template "$REPO_ROOT/install/templates/codex-prompts/memory-review.md.tmpl" \
    --output "$CODEX_HOME/prompts/memory-review.md" \
    --set "REPO_ROOT=$REPO_ROOT" \
    --set "STATE_ROOT=$STATE_DIR"
  step_done
fi

if (( INSTALL_GLOBAL_COMMAND )); then
  step "Installing the global okeep command..."
  mkdir -p "$BIN_DIR"
  "$PYTHON_BIN" "$REPO_ROOT/install/render_template.py" \
    --template "$REPO_ROOT/install/templates/bin/okeep.tmpl" \
    --output "$BIN_DIR/okeep" \
    --set "REPO_ROOT=$REPO_ROOT" \
    --set "STATE_ROOT=$STATE_DIR" \
    --set "CODEX_HOME=$CODEX_HOME" \
    --set "PYTHON_BIN=$PYTHON_BIN" \
    --set "ACTIVITY_SOURCE=$ACTIVITY_SOURCE"
  chmod +x "$BIN_DIR/okeep"
  if ! path_contains_dir "$BIN_DIR"; then
    "$PYTHON_BIN" "$REPO_ROOT/install/configure_shell_path.py" \
      --config "$SHELL_RC_PATH" \
      --path-entry "$BIN_DIR"
    PATH_EXPORT_ADDED=1
  fi
  step_done
fi

if [[ "$OSTYPE" == darwin* ]] && (( ENABLE_BACKGROUND_SERVICES || ENABLE_NIGHTLY )); then
  mkdir -p "$HOME/Library/LaunchAgents"

  if (( ENABLE_BACKGROUND_SERVICES )); then
    step "Installing background refresh services..."
    render_plist \
      "io.github.openkeepsake.overview-refresh.plist.tmpl" \
      "$HOME/Library/LaunchAgents/io.github.openkeepsake.overview-refresh.plist"
    bootstrap_launch_agent \
      "$HOME/Library/LaunchAgents/io.github.openkeepsake.overview-refresh.plist" \
      "io.github.openkeepsake.overview-refresh"

    render_plist \
      "io.github.openkeepsake.token-live.plist.tmpl" \
      "$HOME/Library/LaunchAgents/io.github.openkeepsake.token-live.plist"
    bootstrap_launch_agent \
      "$HOME/Library/LaunchAgents/io.github.openkeepsake.token-live.plist" \
      "io.github.openkeepsake.token-live"
    step_done
  fi

  if (( ENABLE_NIGHTLY )); then
    step "Installing nightly organization services..."
    render_plist \
      "io.github.openkeepsake.nightly-organize.plist.tmpl" \
      "$HOME/Library/LaunchAgents/io.github.openkeepsake.nightly-organize.plist"
    bootstrap_launch_agent \
      "$HOME/Library/LaunchAgents/io.github.openkeepsake.nightly-organize.plist" \
      "io.github.openkeepsake.nightly-organize"

    render_plist \
      "io.github.openkeepsake.nightly-finalize-previous-day.plist.tmpl" \
      "$HOME/Library/LaunchAgents/io.github.openkeepsake.nightly-finalize-previous-day.plist"
    bootstrap_launch_agent \
      "$HOME/Library/LaunchAgents/io.github.openkeepsake.nightly-finalize-previous-day.plist" \
      "io.github.openkeepsake.nightly-finalize-previous-day"
    step_done
  fi
fi

manual_review_command() {
  if (( INSTALL_GLOBAL_COMMAND )); then
    printf 'okeep review --date "$(date +%%F)" --learn-window-days 7\n'
    return
  fi
  printf 'AI_ASSET_STATE_DIR=%q CODEX_HOME=%q AI_ASSET_LANGUAGE=%q OKEEP_ACTIVITY_SOURCE=%q %q %q review --date "$(date +%%F)" --learn-window-days 7\n' \
    "$STATE_DIR" \
    "$CODEX_HOME" \
    "$LANGUAGE" \
    "$ACTIVITY_SOURCE" \
    "$PYTHON_BIN" \
    "$REPO_ROOT/scripts/okeep.py"
}

open_panel_command() {
  if (( INSTALL_GLOBAL_COMMAND )); then
    printf 'okeep open panel\n'
    return
  fi
  printf 'open %q\n' "$STATE_DIR/reports/panel.html"
}

MANUAL_REVIEW_COMMAND="$(manual_review_command)"
OPEN_PANEL_COMMAND="$(open_panel_command)"
if [[ "$MEMORY_MODE" == "codex-context" ]]; then
  REVIEW_CONTEXT_NOTE_ZH="这一步可能需要一点时间；它会先补齐最近 7 天缺失或非 final 的日报，且不会给每个历史日期再扩展学习窗口；随后生成本地 memory / overview。当前 codex-context 会同步 bounded summary，但不会把原始窗口写进 Codex 原生 memory。"
  REVIEW_CONTEXT_NOTE_EN="This can take a while; it first backfills missing or non-final daily reports in the last 7 days without expanding each historical day into another learning window, then updates local memory / overview. The current codex-context mode syncs a bounded summary, but does not write raw windows into Codex native memory."
else
  REVIEW_CONTEXT_NOTE_ZH="这一步可能需要一点时间；它会先补齐最近 7 天缺失或非 final 的日报，且不会给每个历史日期再扩展学习窗口；随后生成本地 memory / overview。当前 $MEMORY_MODE 不会向 Codex context 同步摘要。"
  REVIEW_CONTEXT_NOTE_EN="This can take a while; it first backfills missing or non-final daily reports in the last 7 days without expanding each historical day into another learning window, then updates local memory / overview. The current $MEMORY_MODE mode does not sync a summary into Codex context."
fi

if [[ "$LANGUAGE" == "zh" ]]; then
  cat <<EOF
OpenKeepsake 已安装完成。

安装信息：
  安装模式: $INSTALL_PROFILE
  Repo root: $REPO_ROOT
  State root: $STATE_DIR
  Codex home: $CODEX_HOME
  语言: $LANGUAGE
  记忆模式: $MEMORY_MODE
  活动来源: $ACTIVITY_SOURCE
  面板: $STATE_DIR/reports/panel.html

建议下一步：
  1. 先打开可视化面板：
     $OPEN_PANEL_COMMAND
  2. 可选：需要补齐最近 7 天本地记忆时再运行：
     $MANUAL_REVIEW_COMMAND
     $REVIEW_CONTEXT_NOTE_ZH
     如果要读取 Codex 应用线程，安装时请显式加 --read-codex-app 或 --activity-source auto；默认只读取稳定的 Codex CLI history/session 文件。
EOF

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
  $BIN_DIR/okeep
  常用命令：okeep open panel、okeep core、okeep review
EOF
  fi

  if [[ "$OSTYPE" == darwin* ]] && (( ENABLE_NIGHTLY )); then
    cat <<EOF

后台整理：
  已安装 nightly LaunchAgents：23:00 预览整理，00:10 finalized 前一天结果。
  锁屏可以继续跑；退出登录后用户级 LaunchAgents 不会继续执行。
EOF
  fi
else
  cat <<EOF
Installed OpenKeepsake.

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
  1. Open the visual panel first:
     $OPEN_PANEL_COMMAND
  2. Optional: run this only when you want to backfill a 7-day local-memory window:
     $MANUAL_REVIEW_COMMAND
     $REVIEW_CONTEXT_NOTE_EN
     To read Codex app threads, install with --read-codex-app or --activity-source auto explicitly; by default only the stable Codex CLI history/session files are read.
EOF

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
  $BIN_DIR/okeep
  Common commands: okeep open panel, okeep core, okeep review
EOF
  fi

  if [[ "$OSTYPE" == darwin* ]] && (( ENABLE_NIGHTLY )); then
    cat <<EOF

Background organization:
  Nightly LaunchAgents are installed: preview at 23:00 and previous-day finalize at 00:10.
  A locked screen is fine; logging out stops user-level LaunchAgents.
EOF
  fi
fi

if (( INSTALL_GLOBAL_COMMAND )) && (( PATH_EXPORT_ADDED )); then
  if [[ "$LANGUAGE" == "zh" ]]; then
    cat <<EOF

PATH 提示：
  installer 已把 $BIN_DIR 写入:
    $SHELL_RC_PATH

当前 shell 里如果马上要用 \`okeep\`，先执行：
  export PATH="$BIN_DIR:\$PATH"
EOF
  else
    cat <<EOF

PATH note:
  The installer added $BIN_DIR to PATH in:
    $SHELL_RC_PATH

To use \`okeep\` in the current shell immediately, run:
  export PATH="$BIN_DIR:\$PATH"
EOF
  fi
fi
