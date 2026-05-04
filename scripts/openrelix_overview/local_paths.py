"""Local filesystem path detection and link rendering helpers."""

import re
from html import escape
from pathlib import Path
from urllib.parse import unquote, urlparse

LOCAL_PATH_TRAILING_PUNCTUATION = ".,;!?)]}\"'"
LOCAL_PATH_TOKEN_RE = re.compile(
    r"(file://[^\s<>\"']+|~/[^\s<>\"']+|/[^\s<>\"']+)"
)


def split_path_trailing_punctuation(token):
    core = str(token or "")
    suffix = ""
    while core and core[-1] in LOCAL_PATH_TRAILING_PUNCTUATION:
        suffix = core[-1] + suffix
        core = core[:-1]
    return core, suffix


def strip_line_column_suffix(path_text):
    candidate = str(path_text or "")
    while True:
        stripped = re.sub(r":\d+$", "", candidate)
        if stripped == candidate:
            return candidate
        candidate = stripped


def resolve_local_link_path(raw_path):
    candidate = str(raw_path or "").strip()
    if not candidate:
        return None

    if candidate.startswith("file://"):
        parsed = urlparse(candidate)
        if parsed.scheme != "file":
            return None
        candidate = unquote(parsed.path or "")
        if parsed.netloc and parsed.netloc not in {"", "localhost"}:
            candidate = "//{}{}".format(parsed.netloc, candidate)

    candidate = strip_line_column_suffix(candidate)
    if candidate.startswith("~/"):
        path = Path(candidate).expanduser()
    else:
        path = Path(candidate)

    if not path.is_absolute():
        return None

    try:
        if not path.exists():
            return None
        return path.resolve()
    except OSError:
        return None


def build_local_path_anchor(path, label, class_name="path-link", normalize_text_func=None):
    resolved = resolve_local_link_path(path) if not isinstance(path, Path) else path.resolve()
    normalize_text_func = normalize_text_func or (lambda value: value)
    normalized_label = normalize_text_func(label)
    safe_label = escape("" if normalized_label is None else str(normalized_label))
    if not resolved:
        return safe_label
    return (
        '<a class="{class_name}" href="{href}" target="_blank" rel="noopener noreferrer" title="{title}">{label}</a>'
    ).format(
        class_name=escape(class_name, quote=True),
        href=escape(resolved.as_uri(), quote=True),
        title=escape(str(resolved), quote=True),
        label=safe_label,
    )


def render_detected_local_path_token(token, class_name="path-link", normalize_text_func=None):
    core, suffix = split_path_trailing_punctuation(token)
    resolved = resolve_local_link_path(core)
    if not resolved:
        return None
    return "{}{}".format(
        build_local_path_anchor(
            resolved,
            core,
            class_name=class_name,
            normalize_text_func=normalize_text_func,
        ),
        escape(suffix),
    )


def linkify_local_paths_html(text, class_name="path-link", normalize_text_func=None):
    raw = str(text or "")
    if not raw:
        return ""

    pieces = []
    cursor = 0
    matched = False
    for match in LOCAL_PATH_TOKEN_RE.finditer(raw):
        rendered = render_detected_local_path_token(
            match.group(0),
            class_name=class_name,
            normalize_text_func=normalize_text_func,
        )
        if rendered is None:
            continue
        matched = True
        pieces.append(escape(raw[cursor:match.start()]))
        pieces.append(rendered)
        cursor = match.end()

    if not matched:
        return escape(raw)

    pieces.append(escape(raw[cursor:]))
    return "".join(pieces)


def extract_resolved_local_paths(text, prefer_parent=False):
    paths = []
    seen = set()
    for match in LOCAL_PATH_TOKEN_RE.finditer(str(text or "")):
        resolved = resolve_local_link_path(match.group(0))
        if not resolved:
            continue
        is_file = False
        if prefer_parent:
            try:
                is_file = resolved.is_file()
            except OSError:
                is_file = False
        target = resolved.parent if prefer_parent and is_file else resolved
        key = str(target)
        if key in seen:
            continue
        seen.add(key)
        paths.append(key)
    return paths
