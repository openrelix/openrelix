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


def redact_personal_text(value, patterns=(), redaction_label=PERSONAL_REDACTION_LABEL):
    if not isinstance(value, str):
        return value
    text = value

    protected_file_hrefs = []

    def protect_file_href(match):
        placeholder = "\x00OPENRELIX_FILE_HREF_{}_{}\x00".format(
            len(protected_file_hrefs),
            uuid.uuid4().hex,
        )
        protected_file_hrefs.append((placeholder, match.group(0)))
        return placeholder

    # Keep local file links clickable in the generated local panel while still
    # redacting visible text and title attributes around those links.
    text = re.sub(r"href=([\"'])file://[^\"']+\1", protect_file_href, text)
    text = re.sub(r"href=\\([\"'])file://[^\\]+?\\\1", protect_file_href, text)

    text = re.sub(r"file:///(?:Users|home)/[^/\\\s<>\"']+", "file://~", text)
    text = re.sub(r"(?:/Users|/home)/[^/\\\s<>\"']+", "~", text)

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

    text = re.sub(r"https?://[^\\\s<>\"']+", redact_url, text)
    for pattern in patterns:
        text = pattern.sub(redaction_label, text)
    for placeholder, original in protected_file_hrefs:
        text = text.replace(placeholder, original)
    return text


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
    for source, target in brand_replacements:
        text = text.replace(source, target)
    for phrase in legacy_phrases:
        text = text.replace(phrase, brand_display_name)
    text = re.sub(r"\bAPA\b", brand_display_name, text)
    return redact_personal_text(text, patterns=patterns, redaction_label=redaction_label)


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
