"""Small shared helpers for overview data shaping."""

from datetime import datetime

from asset_runtime import normalize_language


def current_language(language=None, default="zh"):
    return normalize_language(language or default)


def is_english(language=None):
    return current_language(language) == "en"


def localized(zh_text, en_text="", language=None):
    if not is_english(language):
        return zh_text
    return en_text or str(zh_text or "")


def current_local_datetime():
    return datetime.now().astimezone()


def parse_iso_datetime(value):
    if not value:
        return None
    normalized = value
    if isinstance(normalized, str) and normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed.astimezone()


def display_local_datetime(value):
    parsed = value if isinstance(value, datetime) else parse_iso_datetime(value)
    if parsed is None:
        return ""
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def display_short_local_datetime(value):
    parsed = value if isinstance(value, datetime) else parse_iso_datetime(value)
    if parsed is None:
        return ""
    return parsed.strftime("%m-%d %H:%M")


def safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def compact_number(value):
    number = safe_int(value)
    abs_number = abs(number)
    if abs_number >= 1_000_000_000:
        return "{:.1f}B".format(number / 1_000_000_000)
    if abs_number >= 1_000_000:
        return "{:.1f}M".format(number / 1_000_000)
    if abs_number >= 1_000:
        return "{:.1f}K".format(number / 1_000)
    return str(number)


def compact_token_zh(value):
    number = safe_int(value)
    abs_number = abs(number)
    if abs_number >= 100_000_000:
        return "{:.1f}亿".format(number / 100_000_000)
    if abs_number >= 10_000:
        return "{:.1f}万".format(number / 10_000)
    return str(number)


def compact_token(value, language=None):
    if is_english(language):
        return compact_number(value)
    return compact_token_zh(value)


def compact_token_k(value):
    number = safe_int(value)
    if number == 0:
        return "0K"
    value_k = number / 1000
    if number % 1000 == 0:
        return "{}K".format(number // 1000)
    return "{:.1f}K".format(value_k)


def format_percent(value, digits=0, signed=False):
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if signed and number > 0 else ""
    return "{}{:.{digits}f}%".format(sign, number, digits=digits)


def percent_of(part, total):
    total = safe_int(total)
    if total <= 0:
        return None
    return (safe_int(part) / total) * 100
