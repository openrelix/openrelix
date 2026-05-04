"""Language and translation helpers for overview rendering."""

import json

from .common import current_language as normalize_current_language
from .common import safe_int


def current_language(language=None, default_language="zh"):
    return normalize_current_language(language, default=default_language)


def is_english(language=None, default_language="zh"):
    return current_language(language, default_language=default_language) == "en"


def localized(zh_text, en_text="", language=None, translations=None, default_language="zh"):
    if not is_english(language, default_language=default_language):
        return zh_text
    if en_text:
        return en_text
    return (translations or {}).get(str(zh_text or ""), str(zh_text or ""))


def plural_en(count, singular, plural=None):
    number = safe_int(count)
    word = singular if number == 1 else (plural or "{}s".format(singular))
    return "{} {}".format(number, word)


def panel_i18n_json(translations):
    return json.dumps(translations or {}, ensure_ascii=False).replace("</", "<\\/")
