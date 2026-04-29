#!/bin/zsh
set -euo pipefail

DRY_RUN=0
YES=0
USE_DEFAULTS=1
candidates=()
typeset -A seen

usage() {
  cat <<'EOF'
Usage: scripts/cleanup_smoke_temp.sh [options]

Clean temporary directories created by scripts/smoke_temp_panel.sh.

Options:
  --dry-run           List matching directories without deleting them.
  --yes               Delete matching directories without prompting.
  --state-dir PATH    Clean a specific smoke state directory.
  --codex-home PATH   Clean a specific smoke CODEX_HOME directory.
  -h, --help          Show this help.

By default, the script targets:
  /private/tmp/openrelix-smoke.*
  /private/tmp/openrelix-codex-smoke.*
EOF
}

is_safe_smoke_dir() {
  local path="$1"
  local base="${path:t}"
  [[ "${path:h}" == "/private/tmp" ]] || return 1
  case "$base" in
    openrelix-smoke|openrelix-smoke.*|openrelix-smoke-*|openrelix-codex-smoke|openrelix-codex-smoke.*|openrelix-codex-smoke-*)
      return 0
      ;;
  esac
  return 1
}

add_candidate() {
  local raw_path="$1"
  local path=""

  [[ -n "$raw_path" ]] || return 0
  [[ -d "$raw_path" ]] || return 0

  path="${raw_path:A}"
  if ! is_safe_smoke_dir "$path"; then
    echo "skip unsafe path: $raw_path" >&2
    return 0
  fi

  if [[ -z "${seen[$path]:-}" ]]; then
    candidates+=("$path")
    seen[$path]=1
  fi
}

while (( $# > 0 )); do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --yes)
      YES=1
      shift
      ;;
    --state-dir|--codex-home)
      USE_DEFAULTS=0
      add_candidate "${2:?missing value for $1}"
      shift 2
      ;;
    --state-dir=*|--codex-home=*)
      USE_DEFAULTS=0
      add_candidate "${1#*=}"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown cleanup argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if (( USE_DEFAULTS )); then
  path=""
  for path in /private/tmp/openrelix-smoke.*(N) /private/tmp/openrelix-codex-smoke.*(N); do
    add_candidate "$path"
  done
fi

if (( ${#candidates[@]} == 0 )); then
  echo "no smoke temp directories found"
  exit 0
fi

echo "smoke temp directories:"
printf '  %s\n' "${candidates[@]}"

if (( DRY_RUN )); then
  echo
  echo "dry run only; nothing deleted"
  exit 0
fi

if (( ! YES )); then
  if [[ ! -t 0 ]]; then
    echo "refusing to delete without --yes in a non-interactive shell" >&2
    exit 2
  fi
  local answer=""
  printf 'Delete these directories? [y/N] '
  read -r answer
  case "${answer:l}" in
    y|yes)
      ;;
    *)
      echo "aborted"
      exit 0
      ;;
  esac
fi

path=""
for path in "${candidates[@]}"; do
  rm -rf -- "$path"
done

echo "deleted ${#candidates[@]} smoke temp directories"
