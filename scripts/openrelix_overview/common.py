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


def compact_with_units(value, unit_specs, max_digits=3):
    number = safe_int(value)
    if number == 0:
        return "0"
    sign = "-" if number < 0 else ""
    abs_number = abs(number)
    selected_index = None
    for index, (divisor, _unit) in enumerate(unit_specs):
        if abs_number >= divisor:
            selected_index = index
            break
    if selected_index is None:
        return "{}{}".format(sign, abs_number)

    def format_scaled(divisor):
        scaled = abs_number / divisor
        integer_digits = len(str(int(scaled))) if scaled >= 1 else 1
        decimals = max(0, max_digits - integer_digits)
        text = "{:.{}f}".format(scaled, decimals)
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text

    text = format_scaled(unit_specs[selected_index][0])
    digit_count = len([char for char in text if char.isdigit()])
    while digit_count > max_digits and selected_index > 0:
        selected_index -= 1
        text = format_scaled(unit_specs[selected_index][0])
        digit_count = len([char for char in text if char.isdigit()])
    return "{}{}{}".format(sign, text, unit_specs[selected_index][1])


def compact_token_zh(value):
    return compact_with_units(
        value,
        (
            (100_000_000, "亿"),
            (10_000, "万"),
        ),
    )


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
