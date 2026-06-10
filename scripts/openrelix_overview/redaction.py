"""Personal redaction and public brand display normalization."""

import os
import re
import uuid
from pathlib import Path

from asset_runtime import get_runtime_paths

PERSONAL_REDACTION_LABEL = "Work project"
BRAND_DISPLAY_NAME = "OpenRelix"
LEGACY_BRAND_PHRASES = (
    "AI Personal Assets System",
    "AI personal assets system",
    "AI-Personal-Assets",
    "AI Personal Assets",
    "AI personal assets",
    "AI个人资产系统",
    "AI个人资产",
    "AI 个人资产系统",
    "AI 个人资产",
    "ai-personal-assets",
)


LOCAL_EXECUTION_ATTR_PATTERNS = (
    r"href=([\"'])file://[^\"']+\1",
    r"href=\\([\"'])file://[^\\]+?\\\1",
    r"data-open-finder-path=([\"'])[^\"']+\1",
    r"data-open-finder-path=\\([\"'])[^\\]+?\\\1",
    r"data-skill-quarantine-project-candidate=([\"'])[^\"']+\1",
    r"data-skill-quarantine-project-candidate=\\([\"'])[^\\]+?\\\1",
    r"data-skill-quarantine-project-path=([\"'])[^\"']+\1",
    r"data-skill-quarantine-project-path=\\([\"'])[^\\]+?\\\1",
    r"data-skill-quarantine-project-root=([\"'])[^\"']+\1",
    r"data-skill-quarantine-project-root=\\([\"'])[^\\]+?\\\1",
    r"data-resume-command=([\"'])[^\"']+\1",
    r"data-resume-command=\\([\"'])[^\\]+?\\\1",
    r"data-claude-cwd=([\"'])[^\"']+\1",
    r"data-claude-cwd=\\([\"'])[^\\]+?\\\1",
)
LOCAL_EXECUTION_ATTR_PLACEHOLDER_RE = re.compile(
    r"\x00OPENRELIX_LOCAL_ATTR_\d+_[0-9a-f]{32}\x00"
)
LOCAL_EXECUTION_ATTR_RES = tuple(
    re.compile(pattern) for pattern in LOCAL_EXECUTION_ATTR_PATTERNS
)
FILE_URL_HOME_RE = re.compile(r"file:///(?:Users|home)/[^/\\\s<>\"']+")
LOCAL_HOME_PATH_RE = re.compile(r"(?:/Users|/home)/[^/\\\s<>\"']+")
HTTP_URL_RE = re.compile(r"https?://[^\\\s<>\"']+")
LEGACY_APA_RE = re.compile(r"\bAPA\b")


def protect_local_execution_attrs(text):
    # Every protected attribute pattern requires one of these literals, so
    # plain text (the overwhelmingly common case) can skip all regex work.
    if "file://" not in text and "data-" not in text:
        return text, []

    protected = []

    def protect(match):
        placeholder = "\x00OPENRELIX_LOCAL_ATTR_{}_{}\x00".format(
            len(protected),
            uuid.uuid4().hex,
        )
        protected.append((placeholder, match.group(0)))
        return placeholder

    for pattern in LOCAL_EXECUTION_ATTR_RES:
        text = pattern.sub(protect, text)
    return text, protected


def restore_protected_text(text, protected):
    if not protected:
        return text
    replacements = dict(protected)
    return LOCAL_EXECUTION_ATTR_PLACEHOLDER_RE.sub(
        lambda match: replacements.get(match.group(0), match.group(0)),
        text,
    )


def load_personal_redaction_patterns(paths=None, denylist_env_var="OPENRELIX_PERSONAL_DENYLIST"):
    paths = paths or get_runtime_paths()
    candidates = []
    explicit = os.environ.get(denylist_env_var)
    if explicit:
        candidates.append(Path(explicit).expanduser())
    try:
        candidates.append(paths.state_root / "personal_denylist.txt")
    except Exception:
        pass

    compiled = []
    seen = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        try:
            lines = resolved.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                compiled.append(re.compile(line))
            except re.error:
                continue
    return tuple(compiled)


def redact_personal_text(
    value,
    patterns=(),
    redaction_label=PERSONAL_REDACTION_LABEL,
    protect_local_attrs=True,
):
    if not isinstance(value, str):
        return value
    text = value

    # Keep local file links clickable in the generated local panel while still
    # redacting visible text and title attributes around those links. Finder
    # reveal buttons need the same treatment because the hidden path is the
    # action payload, not display text.
    protected_local_attrs = ()
    if protect_local_attrs:
        text, protected_local_attrs = protect_local_execution_attrs(text)

    if "file:///" in text:
        text = FILE_URL_HOME_RE.sub("file://~", text)
    if "/Users/" in text or "/home/" in text:
        text = LOCAL_HOME_PATH_RE.sub("~", text)

    def redact_url(match):
        url = match.group(0)
        lowered = url.lower()
        if (
            lowered.startswith("http://127.")
            or lowered.startswith("http://localhost")
            or lowered.startswith("https://github.com/openrelix/")
            or lowered.startswith("https://openrelix.org")
            or lowered == "https://registry.npmjs.org/"
            or lowered.startswith("https://www.npmjs.com/~kk_kais")
        ):
            return url
        return "<link>"

    if "http" in text:
        text = HTTP_URL_RE.sub(redact_url, text)
    for pattern in patterns:
        text = pattern.sub(redaction_label, text)
    return restore_protected_text(text, protected_local_attrs)


def normalize_brand_display_text(
    value,
    brand_replacements=(),
    legacy_phrases=LEGACY_BRAND_PHRASES,
    brand_display_name=BRAND_DISPLAY_NAME,
    patterns=(),
    redaction_label=PERSONAL_REDACTION_LABEL,
):
    if not isinstance(value, str):
        return value
    text = str(value or "")
    if not text:
        return text
    text, protected_local_attrs = protect_local_execution_attrs(text)
    for source, target in brand_replacements:
        text = text.replace(source, target)
    for phrase in legacy_phrases:
        text = text.replace(phrase, brand_display_name)
    if "APA" in text:
        text = LEGACY_APA_RE.sub(brand_display_name, text)
    text = redact_personal_text(
        text,
        patterns=patterns,
        redaction_label=redaction_label,
        protect_local_attrs=False,
    )
    return restore_protected_text(text, protected_local_attrs)


def normalize_brand_display_payload(value, normalize_text_func):
    if isinstance(value, dict):
        return {
            normalize_text_func(key): normalize_brand_display_payload(item, normalize_text_func)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [normalize_brand_display_payload(item, normalize_text_func) for item in value]
    if isinstance(value, tuple):
        return tuple(normalize_brand_display_payload(item, normalize_text_func) for item in value)
    return normalize_text_func(value)
