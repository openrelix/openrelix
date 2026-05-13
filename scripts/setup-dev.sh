#!/bin/sh
# OpenRelix one-time dev setup. Run once after cloning the repo.
#
# What it does:
# - Points git's hook path at scripts/git-hooks/, so pre-commit and any
#   future hooks run automatically without per-hook symlinks.
#
# Scope:
# - Writes to .git/config (repository-wide). All worktrees of this clone
#   share the same hooks path — that is the desired behavior.
# - Any previous manual symlink at .git/hooks/pre-commit becomes inert
#   once core.hooksPath is set. Behavior does not regress (the hook still
#   runs from scripts/git-hooks/); the old symlink is just unused.
#   Safe to delete: rm .git/hooks/pre-commit
#
# Safe to re-run. Does not write user state, host config, or runtime data.

set -e

if ! command -v git >/dev/null 2>&1; then
    echo "setup-dev: git is required" >&2
    exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$REPO_ROOT" ]; then
    echo "setup-dev: not inside a git repository" >&2
    exit 1
fi

cd "$REPO_ROOT"

HOOKS_DIR="scripts/git-hooks"
if [ ! -d "$HOOKS_DIR" ]; then
    echo "setup-dev: $HOOKS_DIR not found" >&2
    exit 1
fi

git config core.hooksPath "$HOOKS_DIR"
chmod +x "$HOOKS_DIR"/* 2>/dev/null || true

echo "setup-dev: core.hooksPath -> $HOOKS_DIR"
echo "setup-dev: pre-commit hook will run scripts/check_personal_info.py on every commit"
echo "setup-dev: set OPENRELIX_SKIP_PERSONAL_CHECK=1 to bypass for a single commit"
