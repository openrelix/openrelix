"""Pure helpers for OpenRelix managed memory registry views."""

import re

from .common import parse_iso_datetime


def normalize_memory_signature_text(text):
    compact = " ".join(str(text or "").split()).strip().lower()
    if not compact:
        return ""
    compact = re.sub(r"[`\"'“”‘’]+", "", compact)
    return compact


def build_memory_group_key(item, bucket=""):
    bucket_value = bucket or item.get("bucket", "") or "unknown"
    memory_type = item.get("memory_type", "") or "semantic"
    primary_text = item.get("title", "") or item.get("value_note", "")
    normalized = normalize_memory_signature_text(primary_text) or "untitled"
    return "{}::{}::{}".format(bucket_value, memory_type, normalized)


def memory_sort_key(value):
    parsed = parse_iso_datetime(value)
    if parsed is not None:
        return parsed.isoformat()
    return str(value or "")


def display_memory_date(value, unknown_label="时间未知"):
    parsed = parse_iso_datetime(value)
    if parsed is not None:
        return parsed.strftime("%Y-%m-%d")
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else (text or unknown_label)
