#!/bin/zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

SCRIPT_DIR="${0:A:h}"

target_date="$(date +%F)"

/bin/zsh "$SCRIPT_DIR/nightly_pipeline.sh" "$target_date" preliminary
