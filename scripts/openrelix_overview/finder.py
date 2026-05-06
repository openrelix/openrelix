"""Open local files from the panel through Finder."""

import subprocess
import sys
from pathlib import Path


FINDER_REVEAL_PATH = "/open-finder"


def normalize_reveal_path(raw_path):
    text = str(raw_path or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        return None
    if not path.exists():
        return None
    try:
        return path.resolve(strict=False)
    except OSError:
        return path


def reveal_path_in_finder(raw_path):
    path = normalize_reveal_path(raw_path)
    if path is None:
        return {"ok": False, "error": "path_not_found"}
    if sys.platform != "darwin":
        return {"ok": False, "error": "finder_unsupported_platform", "path": str(path)}
    try:
        subprocess.Popen(
            ["open", "-R", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        return {"ok": False, "error": "finder_open_failed", "detail": str(exc), "path": str(path)}
    return {"ok": True, "status": "opening", "path": str(path)}
