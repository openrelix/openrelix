"""Runtime update-token storage for the local panel update endpoint."""

import os
import secrets

from asset_runtime import get_runtime_paths


def update_token_path(paths=None):
    paths = paths or get_runtime_paths()
    return paths.runtime_dir / "update-token.txt"


def read_or_create_update_token(paths=None, path=None):
    """Return the persistent shared secret for the local /run-update endpoint."""
    token_path = path or update_token_path(paths)
    try:
        text = token_path.read_text(encoding="utf-8").strip()
        if text:
            return text
    except (OSError, UnicodeDecodeError):
        pass

    token = secrets.token_urlsafe(32)
    try:
        token_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = token_path.with_suffix(".tmp")
        tmp.write_text(token, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(str(tmp), str(token_path))
    except OSError:
        pass
    return token

