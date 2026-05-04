"""Pure token-usage view shaping for overview and token-live server."""

import math
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from .common import (
    compact_token,
    current_language,
    current_local_datetime,
    display_local_datetime,
    format_percent,
    localized,
    percent_of,
    safe_float,
    safe_int,
)
from .config import CCUSAGE_WINDOW_DAYS


def make_token_breakdown_detail(label, value, meta="", language=None):
    return {
        "label": label,
        "value": safe_int(value),
        "title": "{}：{}".format(label, compact_token(value, language=language)),
        "meta": meta,
    }


def split_ccusage_input_tokens(row):
    total_input_tokens = safe_int(row.get("inputTokens", 0))
    cached_input_tokens = safe_int(row.get("cachedInputTokens", 0))
    uncached_input_tokens = max(total_input_tokens - cached_input_tokens, 0)
    return total_input_tokens, cached_input_tokens, uncached_input_tokens


def build_token_breakdown_details(row, language=None):
    language = current_language(language)
    total_tokens = row.get("totalTokens", 0)
    total_input_tokens, cached_input_tokens, input_tokens = split_ccusage_input_tokens(row)
    output_tokens = row.get("outputTokens", 0)
    reasoning_output_tokens = row.get("reasoningOutputTokens", 0)
    cached_share = percent_of(cached_input_tokens, total_input_tokens)
    output_share = percent_of(output_tokens, total_tokens)
    reasoning_share = percent_of(reasoning_output_tokens, total_tokens)
    details = [
        make_token_breakdown_detail(
            localized("输入", "Input", language),
            input_tokens,
            localized("无缓存输入 Token", "Uncached input tokens", language),
            language=language,
        ),
        make_token_breakdown_detail(
            localized("缓存读取", "Cache Read", language),
            cached_input_tokens,
            localized(
                "占总输入 {}".format(format_percent(cached_share)),
                "{} of total input".format(format_percent(cached_share)),
                language,
            ),
            language=language,
        ),
        make_token_breakdown_detail(
            localized("输出", "Output", language),
            output_tokens,
            localized(
                "占总量 {}".format(format_percent(output_share, digits=1)),
                "{} of total".format(format_percent(output_share, digits=1)),
                language,
            ),
            language=language,
        ),
        make_token_breakdown_detail(
            localized("推理输出", "Reasoning output", language),
            reasoning_output_tokens,
            localized(
                "占总量 {}".format(format_percent(reasoning_share, digits=1)),
                "{} of total".format(format_percent(reasoning_share, digits=1)),
                language,
            ),
            language=language,
        ),
    ]
    cost = row.get("costUSD")
    if isinstance(cost, (int, float)) and cost > 0:
        details.append(
            {
                "title": localized(
                    "费用估算：${:.2f}".format(cost),
                    "Estimated cost: ${:.2f}".format(cost),
                    language,
                ),
                "meta": localized("来自 ccusage", "From ccusage", language),
            }
        )
    return details


def make_token_summary_card(label, value, caption, tone="neutral"):
    return {
        "label": label,
        "value": value,
        "caption": caption,
        "tone": tone,
    }


def format_usd(value):
    amount = safe_float(value)
    if amount <= 0 or not math.isfinite(amount):
        return "—"
    rounded = Decimal(str(amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return "${:,}".format(int(rounded))


def compact_token_with_cost(token_value, cost_value, language=None):
    token_display = compact_token(token_value, language=language)
    cost_display = format_usd(cost_value)
    if cost_display == "—":
        return token_display
    return "{} · {}".format(token_display, cost_display)


def build_token_summary_cards(parsed_rows, trailing_rows, latest, language=None):
    language = current_language(language)
    if not latest:
        return []

    active_trailing_rows = [row for row in trailing_rows if row.get("totalTokens", 0) > 0]
    summary_cards = []

    if active_trailing_rows:
        seven_day_total = sum(row["totalTokens"] for row in active_trailing_rows)
        seven_day_cost = sum(safe_float(row.get("costUSD")) for row in active_trailing_rows)
        seven_day_average = sum(row["totalTokens"] for row in active_trailing_rows) // len(active_trailing_rows)
        peak_row = max(active_trailing_rows, key=lambda row: row["totalTokens"])
        summary_cards.extend(
            [
                make_token_summary_card(
                    localized("7 日账单", "7-day bill", language),
                    format_usd(seven_day_cost),
                    localized(
                        "{} Token · ccusage 估算".format(compact_token(seven_day_total, language=language)),
                        "{} Tokens · ccusage estimate".format(compact_token(seven_day_total, language=language)),
                        language,
                    ),
                ),
                make_token_summary_card(
                    localized("7 日均值", "7-day average", language),
                    compact_token(seven_day_average, language=language),
                    localized(
                        "按 {} 个有数据日".format(len(active_trailing_rows)),
                        "Across {} days with data".format(len(active_trailing_rows)),
                        language,
                    ),
                ),
                make_token_summary_card(
                    localized("峰值日", "Peak day", language),
                    compact_token(peak_row["totalTokens"], language=language),
                    localized(
                        "{} 最高".format(peak_row["date_label"]),
                        "Peak on {}".format(peak_row["date_label"]),
                        language,
                    ),
                ),
            ]
        )
    else:
        summary_cards.append(
            make_token_summary_card(
                localized("7 日账单", "7-day bill", language),
                "—",
                localized("暂无 7 日账单数据", "No 7-day bill data yet", language),
            )
        )

    total_input_tokens, cached_input_tokens, _ = split_ccusage_input_tokens(latest)
    cached_share = percent_of(cached_input_tokens, total_input_tokens)
    summary_cards.append(
        make_token_summary_card(
            localized("缓存读取占总输入", "Cache Read / total input", language),
            format_percent(cached_share),
            localized(
                "缓存读取 {} / 总输入 {}".format(
                    compact_token(cached_input_tokens, language=language),
                    compact_token(total_input_tokens, language=language),
                ),
                "Cache Read {} / total input {}".format(
                    compact_token(cached_input_tokens, language=language),
                    compact_token(total_input_tokens, language=language),
                ),
                language,
            ),
            "neutral",
        )
    )
    return summary_cards


def token_daily_tone(value, max_value):
    value = safe_int(value)
    max_value = max(safe_int(max_value), 1)
    if value <= 0:
        return "token-daily-empty"

    ratio = value / max_value
    if ratio >= 0.85:
        return "token-daily-high"
    if ratio >= 0.45:
        return "token-daily-mid"
    return "token-daily-low"


def token_breakdown_tone(kind):
    return {
        "input": "token-input",
        "cached_input": "token-cache",
        "output": "token-output",
        "reasoning_output": "token-reasoning",
    }.get(kind, "token-input")


def recent_token_daily_rows(
    parsed_rows,
    window_days=CCUSAGE_WINDOW_DAYS,
    now_func=current_local_datetime,
):
    window_days = max(safe_int(window_days), 1)
    if not parsed_rows:
        return []
    if any(row.get("parsed_date") for row in parsed_rows):
        start_date = now_func().date() - timedelta(days=window_days - 1)
        rows = [
            row for row in parsed_rows
            if not row.get("parsed_date") or row["parsed_date"].date() >= start_date
        ]
    else:
        rows = list(parsed_rows)
    return rows[-window_days:]


def build_token_usage_view(
    ccusage_result,
    language=None,
    now_func=current_local_datetime,
):
    language = current_language(language)
    window_days = max(safe_int(ccusage_result.get("window_days", CCUSAGE_WINDOW_DAYS)), 1)
    refreshed_at = ccusage_result.get("fetched_at", "")
    refreshed_at_display = display_local_datetime(refreshed_at)
    if not ccusage_result["available"]:
        return {
            "available": False,
            "error": ccusage_result.get("error", ""),
            "daily_rows": [],
            "today_breakdown": [],
            "today_total_tokens": None,
            "today_total_tokens_display": "—",
            "seven_day_total_tokens": None,
            "seven_day_total_tokens_display": "—",
            "seven_day_cost_usd": None,
            "seven_day_cost_display": "—",
            "today_date_label": localized("今日", "Today", language),
            "summary_cards": [],
            "overview_note": localized(
                "等待实时刷新 Token 统计",
                "Waiting for live Token stats",
                language,
            ),
            "refreshed_at": refreshed_at,
            "refreshed_at_display": refreshed_at_display,
            "window_days": window_days,
            "provider": ccusage_result.get("provider", "all"),
            "provider_label": ccusage_result.get("provider_label", "ccusage"),
            "provider_results": ccusage_result.get("provider_results", {}),
            "partial": bool(ccusage_result.get("partial")),
        }

    raw_rows = ccusage_result.get("payload", {}).get("daily", [])
    parsed_rows = []
    for row in raw_rows:
        raw_date = row.get("date", "")
        parsed_date = None
        for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%Y%m%d"):
            try:
                parsed_date = datetime.strptime(raw_date, fmt)
                break
            except ValueError:
                continue
        label = parsed_date.strftime("%m-%d") if parsed_date else raw_date
        total_input_tokens = safe_int(row.get("inputTokens", 0))
        cached_input_tokens = safe_int(row.get("cachedInputTokens", 0))
        uncached_input_tokens = max(total_input_tokens - cached_input_tokens, 0)
        parsed_rows.append(
            {
                "raw_date": raw_date,
                "date_label": label,
                "sort_key": parsed_date.isoformat() if parsed_date else raw_date,
                "parsed_date": parsed_date,
                "inputTokens": total_input_tokens,
                "totalInputTokens": total_input_tokens,
                "uncachedInputTokens": uncached_input_tokens,
                "cachedInputTokens": cached_input_tokens,
                "outputTokens": safe_int(row.get("outputTokens", 0)),
                "reasoningOutputTokens": safe_int(row.get("reasoningOutputTokens", 0)),
                "totalTokens": safe_int(row.get("totalTokens", 0)),
                "display_total_tokens": compact_token(row.get("totalTokens", 0), language=language),
                "costUSD": safe_float(row.get("costUSD", 0)),
                "provider": row.get("provider", ccusage_result.get("provider", "codex")),
                "providerLabel": row.get("providerLabel", ccusage_result.get("provider_label", "")),
                "providers": row.get("providers", {}),
            }
        )

    parsed_rows.sort(key=lambda item: item["sort_key"])
    parsed_rows = recent_token_daily_rows(parsed_rows, window_days=window_days, now_func=now_func)
    max_daily_tokens = max((row["totalTokens"] for row in parsed_rows), default=0)
    latest = parsed_rows[-1] if parsed_rows else None
    trailing = parsed_rows[-7:]
    seven_day_total = sum(item["totalTokens"] for item in trailing)
    seven_day_cost = sum(safe_float(item.get("costUSD")) for item in trailing)
    active_trailing_count = sum(1 for item in trailing if item["totalTokens"] > 0)
    overview_note = localized(
        "近 {} 天中 {} 天有记录 · {} · {}".format(
            window_days,
            active_trailing_count,
            ccusage_result.get("provider_label", "ccusage"),
            refreshed_at_display or "等待实时刷新",
        ),
        "{} days with records in the last {} days · {} · {}".format(
            active_trailing_count,
            window_days,
            ccusage_result.get("provider_label", "ccusage"),
            refreshed_at_display or "waiting for live refresh",
        ),
        language,
    )

    today_breakdown = []
    if latest:
        total_input_tokens, cached_input_tokens, uncached_input_tokens = split_ccusage_input_tokens(latest)
        cached_share = percent_of(cached_input_tokens, total_input_tokens)
        output_share = percent_of(latest["outputTokens"], latest["totalTokens"])
        reasoning_share = percent_of(latest["reasoningOutputTokens"], latest["totalTokens"])
        today_breakdown = [
            {
                "label": localized("输入", "Input", language),
                "value": uncached_input_tokens,
                "display": compact_token(uncached_input_tokens, language=language),
                "tone": token_breakdown_tone("input"),
                "details": [
                    {
                        "label": localized("输入", "Input", language),
                        "value": uncached_input_tokens,
                        "title": localized(
                            "输入：{}".format(compact_token(uncached_input_tokens, language=language)),
                            "Input: {}".format(compact_token(uncached_input_tokens, language=language)),
                            language,
                        ),
                        "meta": localized("无缓存输入 Token", "Uncached input tokens", language),
                    },
                ],
                "details_heading": localized("输入详情", "Input details", language),
            },
            {
                "label": localized("缓存读取", "Cache Read", language),
                "value": latest["cachedInputTokens"],
                "display": compact_token(latest["cachedInputTokens"], language=language),
                "tone": token_breakdown_tone("cached_input"),
                "details": [
                    {
                        "label": localized("缓存读取", "Cache Read", language),
                        "value": latest["cachedInputTokens"],
                        "title": localized(
                            "缓存读取：{}".format(compact_token(latest["cachedInputTokens"], language=language)),
                            "Cache Read: {}".format(compact_token(latest["cachedInputTokens"], language=language)),
                            language,
                        ),
                        "meta": localized(
                            "占总输入 {}".format(format_percent(cached_share)),
                            "{} of total input".format(format_percent(cached_share)),
                            language,
                        ),
                    },
                ],
                "details_heading": localized("缓存详情", "Cache details", language),
            },
            {
                "label": localized("输出", "Output", language),
                "value": latest["outputTokens"],
                "display": compact_token(latest["outputTokens"], language=language),
                "tone": token_breakdown_tone("output"),
                "details": [
                    {
                        "label": localized("输出", "Output", language),
                        "value": latest["outputTokens"],
                        "title": localized(
                            "输出：{}".format(compact_token(latest["outputTokens"], language=language)),
                            "Output: {}".format(compact_token(latest["outputTokens"], language=language)),
                            language,
                        ),
                        "meta": localized(
                            "占总量 {}".format(format_percent(output_share, digits=1)),
                            "{} of total".format(format_percent(output_share, digits=1)),
                            language,
                        ),
                    },
                ],
                "details_heading": localized("输出详情", "Output details", language),
            },
            {
                "label": localized("推理输出", "Reasoning output", language),
                "value": latest["reasoningOutputTokens"],
                "display": compact_token(latest["reasoningOutputTokens"], language=language),
                "tone": token_breakdown_tone("reasoning_output"),
                "details": [
                    {
                        "label": localized("推理输出", "Reasoning output", language),
                        "value": latest["reasoningOutputTokens"],
                        "title": localized(
                            "推理输出：{}".format(compact_token(latest["reasoningOutputTokens"], language=language)),
                            "Reasoning output: {}".format(compact_token(latest["reasoningOutputTokens"], language=language)),
                            language,
                        ),
                        "meta": localized(
                            "占总量 {}".format(format_percent(reasoning_share, digits=1)),
                            "{} of total".format(format_percent(reasoning_share, digits=1)),
                            language,
                        ),
                    },
                ],
                "details_heading": localized("推理详情", "Reasoning details", language),
            },
        ]

    return {
        "available": True,
        "error": "",
        "daily_rows": [
            {
                "label": row["date_label"],
                "value": row["totalTokens"],
                "display": compact_token_with_cost(row["totalTokens"], row.get("costUSD"), language=language),
                "token_display": row["display_total_tokens"],
                "costUSD": row.get("costUSD", 0),
                "cost_display": format_usd(row.get("costUSD")),
                "provider": row.get("provider", ""),
                "provider_label": row.get("providerLabel", ""),
                "providers": row.get("providers", {}),
                "tone": token_daily_tone(row["totalTokens"], max_daily_tokens),
                "details": build_token_breakdown_details(row, language=language),
                "details_heading": localized(
                    "{} Token 构成".format(row["date_label"]),
                    "Token breakdown for {}".format(row["date_label"]),
                    language,
                ),
            }
            for row in parsed_rows
        ],
        "today_breakdown": today_breakdown,
        "today_total_tokens": latest["totalTokens"] if latest else 0,
        "today_total_tokens_display": compact_token(latest["totalTokens"], language=language) if latest else "0",
        "seven_day_total_tokens": seven_day_total,
        "seven_day_total_tokens_display": compact_token(seven_day_total, language=language),
        "seven_day_cost_usd": seven_day_cost,
        "seven_day_cost_display": format_usd(seven_day_cost),
        "today_date_label": latest["date_label"] if latest else localized("今日", "Today", language),
        "summary_cards": build_token_summary_cards(parsed_rows, trailing, latest, language=language),
        "overview_note": overview_note,
        "refreshed_at": refreshed_at,
        "refreshed_at_display": refreshed_at_display,
        "window_days": window_days,
        "provider": ccusage_result.get("provider", "codex"),
        "provider_label": ccusage_result.get("provider_label", "ccusage"),
        "provider_results": ccusage_result.get("provider_results", {}),
        "partial": bool(ccusage_result.get("partial")),
    }
