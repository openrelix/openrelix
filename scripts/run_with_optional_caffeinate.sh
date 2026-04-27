#!/bin/zsh
set -euo pipefail

mode="${AI_ASSET_KEEP_AWAKE:-none}"

if [[ "$mode" == "during-job" ]] && [[ -x /usr/bin/caffeinate ]]; then
  exec /usr/bin/caffeinate -dimsu "$@"
fi

exec "$@"
