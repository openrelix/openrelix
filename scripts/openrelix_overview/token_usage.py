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
    cache_creation_tokens = safe_int(row.get("cacheCreationTokens", 0))
    uncached_input_tokens = max(total_input_tokens - cached_input_tokens - cache_creation_tokens, 0)
    return total_input_tokens, cached_input_tokens, uncached_input_tokens


def build_token_breakdown_details(row, language=None):
    language = current_language(language)
    total_tokens = row.get("totalTokens", 0)
    total_input_tokens, cached_input_tokens, input_tokens = split_ccusage_input_tokens(row)
    output_tokens = row.get("outputTokens", 0)
    reasoning_output_tokens = row.get("reasoningOutputTokens", 0)
    cache_creation_tokens = safe_int(row.get("cacheCreationTokens", 0))
    cached_share = percent_of(cached_input_tokens, total_input_tokens)
    cache_creation_share = percent_of(cache_creation_tokens, total_tokens)
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
    if cache_creation_tokens > 0:
        details.append(
            make_token_breakdown_detail(
                localized("缓存写入", "Cache Write", language),
                cache_creation_tokens,
                localized(
                    "占总量 {}".format(format_percent(cache_creation_share, digits=1)),
                    "{} of total".format(format_percent(cache_creation_share, digits=1)),
                    language,
                ),
                language=language,
            )
        )
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


def normalize_token_group_by(group_by=None):
    text = str(group_by or "day").strip().lower().replace("_", "-")
    if text in {"month", "monthly", "months"}:
        return "month"
    return "day"


def parse_token_usage_date(raw_date):
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(str(raw_date or ""), fmt)
        except ValueError:
            continue
    return None


def format_iso_date(value):
    parsed_date = parse_token_usage_date(value)
    return parsed_date.date().isoformat() if parsed_date else str(value or "")


def format_token_range_label(start_date, end_date, group_by="day", language=None):
    language = current_language(language)
    start_text = start_date.date().isoformat() if isinstance(start_date, datetime) else str(start_date or "")
    end_text = end_date.date().isoformat() if isinstance(end_date, datetime) else str(end_date or "")
    if group_by == "month":
        start_text = start_text[:7] if start_text else ""
        end_text = end_text[:7] if end_text else ""
    if start_text and end_text and start_text != end_text:
        return localized("{} 至 {}".format(start_text, end_text), "{} to {}".format(start_text, end_text), language)
    if start_text or end_text:
        return start_text or end_text
    return localized("当前区间", "Current range", language)


def token_period_unit(group_by="day", language=None):
    language = current_language(language)
    return localized("月", "months", language) if group_by == "month" else localized("日", "days", language)


def aggregate_token_rows(rows, label, sort_key, parsed_date, raw_date, group_by, language=None):
    rows = list(rows)
    provider_labels = {
        str(row.get("providerLabel") or row.get("provider_label") or "")
        for row in rows
        if row.get("providerLabel") or row.get("provider_label")
    }
    provider_label = " + ".join(sorted(provider_labels)) if provider_labels else ""
    providers = {}
    for row in rows:
        provider = row.get("provider")
        if provider and provider != "all":
            target = providers.setdefault(
                provider,
                {
                    "provider": provider,
                    "providerLabel": row.get("providerLabel", ""),
                    "inputTokens": 0,
                    "cachedInputTokens": 0,
                    "cacheCreationTokens": 0,
                    "outputTokens": 0,
                    "reasoningOutputTokens": 0,
                    "totalTokens": 0,
                    "costUSD": 0.0,
                },
            )
            for field in (
                "inputTokens",
                "cachedInputTokens",
                "cacheCreationTokens",
                "outputTokens",
                "reasoningOutputTokens",
                "totalTokens",
            ):
                target[field] += safe_int(row.get(field, 0))
            target["costUSD"] += safe_float(row.get("costUSD", 0))
        for provider, provider_row in (row.get("providers") or {}).items():
            target = providers.setdefault(
                provider,
                {
                    "provider": provider,
                    "providerLabel": provider_row.get("providerLabel", ""),
                    "inputTokens": 0,
                    "cachedInputTokens": 0,
                    "cacheCreationTokens": 0,
                    "outputTokens": 0,
                    "reasoningOutputTokens": 0,
                    "totalTokens": 0,
                    "costUSD": 0.0,
                },
            )
            for field in (
                "inputTokens",
                "cachedInputTokens",
                "cacheCreationTokens",
                "outputTokens",
                "reasoningOutputTokens",
                "totalTokens",
            ):
                target[field] += safe_int(provider_row.get(field, 0))
            target["costUSD"] += safe_float(provider_row.get("costUSD", 0))

    total_input_tokens = sum(safe_int(row.get("inputTokens", 0)) for row in rows)
    cached_input_tokens = sum(safe_int(row.get("cachedInputTokens", 0)) for row in rows)
    cache_creation_tokens = sum(safe_int(row.get("cacheCreationTokens", 0)) for row in rows)
    return {
        "raw_date": raw_date,
        "date_label": label,
        "sort_key": sort_key,
        "parsed_date": parsed_date,
        "inputTokens": total_input_tokens,
        "totalInputTokens": total_input_tokens,
        "uncachedInputTokens": max(total_input_tokens - cached_input_tokens - cache_creation_tokens, 0),
        "cachedInputTokens": cached_input_tokens,
        "cacheCreationTokens": cache_creation_tokens,
        "outputTokens": sum(safe_int(row.get("outputTokens", 0)) for row in rows),
        "reasoningOutputTokens": sum(safe_int(row.get("reasoningOutputTokens", 0)) for row in rows),
        "totalTokens": sum(safe_int(row.get("totalTokens", 0)) for row in rows),
        "display_total_tokens": compact_token(sum(safe_int(row.get("totalTokens", 0)) for row in rows), language=language),
        "costUSD": sum(safe_float(row.get("costUSD", 0)) for row in rows),
        "provider": "all" if len(provider_labels) > 1 else (rows[0].get("provider", "") if rows else ""),
        "providerLabel": provider_label or (rows[0].get("providerLabel", "") if rows else ""),
        "providers": providers,
        "group_by": group_by,
        "day_count": len(rows),
        "active_day_count": sum(1 for row in rows if safe_int(row.get("totalTokens", 0)) > 0),
    }


def aggregate_monthly_token_rows(parsed_rows, language=None):
    monthly = {}
    for row in parsed_rows:
        parsed_date = row.get("parsed_date")
        if not parsed_date:
            continue
        key = parsed_date.strftime("%Y-%m")
        monthly.setdefault(key, []).append(row)
    result = []
    for key in sorted(monthly):
        parsed_date = datetime.strptime(key + "-01", "%Y-%m-%d")
        result.append(
            aggregate_token_rows(
                monthly[key],
                key,
                key,
                parsed_date,
                key,
                "month",
                language=language,
            )
        )
    return result


def build_token_summary_cards(
    parsed_rows,
    trailing_rows,
    latest,
    language=None,
    group_by="day",
    custom_period=False,
):
    language = current_language(language)
    if not latest:
        return []

    group_by = normalize_token_group_by(group_by)
    summary_rows = parsed_rows if custom_period else trailing_rows
    active_trailing_rows = [row for row in summary_rows if row.get("totalTokens", 0) > 0]
    bill_label = localized("周期账单", "Period bill", language) if custom_period else localized("7 日账单", "7-day bill", language)
    average_label = localized("月均值", "Monthly average", language) if group_by == "month" else (
        localized("周期日均", "Daily average", language) if custom_period else localized("7 日均值", "7-day average", language)
    )
    peak_label = localized("峰值月", "Peak month", language) if group_by == "month" else localized("峰值日", "Peak day", language)
    summary_cards = []

    if active_trailing_rows:
        seven_day_total = sum(row["totalTokens"] for row in active_trailing_rows)
        seven_day_cost = sum(safe_float(row.get("costUSD")) for row in active_trailing_rows)
        seven_day_average = sum(row["totalTokens"] for row in active_trailing_rows) // len(active_trailing_rows)
        peak_row = max(active_trailing_rows, key=lambda row: row["totalTokens"])
        summary_cards.extend(
            [
                make_token_summary_card(
                    bill_label,
                    format_usd(seven_day_cost),
                    localized(
                        "{} Token · ccusage 估算".format(compact_token(seven_day_total, language=language)),
                        "{} Tokens · ccusage estimate".format(compact_token(seven_day_total, language=language)),
                        language,
                    ),
                ),
                make_token_summary_card(
                    average_label,
                    compact_token(seven_day_average, language=language),
                    localized(
                        "按 {} 个有数据{}".format(len(active_trailing_rows), token_period_unit(group_by, language=language)),
                        "Across {} {} with data".format(len(active_trailing_rows), token_period_unit(group_by, language=language)),
                        language,
                    ),
                ),
                make_token_summary_card(
                    peak_label,
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
                bill_label,
                "—",
                localized("暂无账单数据", "No bill data yet", language),
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
        "cache_creation": "token-cache-write",
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
    group_by=None,
    start_date=None,
    end_date=None,
):
    language = current_language(language)
    group_by = normalize_token_group_by(group_by or ccusage_result.get("group_by"))
    window_days = max(safe_int(ccusage_result.get("window_days", CCUSAGE_WINDOW_DAYS)), 1)
    refreshed_at = ccusage_result.get("fetched_at", "")
    refreshed_at_display = display_local_datetime(refreshed_at)
    requested_start = parse_token_usage_date(start_date or ccusage_result.get("range_start"))
    requested_end = parse_token_usage_date(end_date or ccusage_result.get("range_end"))
    if requested_start and requested_end and requested_start > requested_end:
        requested_start, requested_end = requested_end, requested_start
    if not ccusage_result["available"]:
        range_label = format_token_range_label(requested_start, requested_end, group_by, language=language)
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
            "range_label": range_label,
            "range_start": requested_start.date().isoformat() if requested_start else "",
            "range_end": requested_end.date().isoformat() if requested_end else "",
            "group_by": group_by,
            "period_total_tokens": None,
            "period_total_tokens_display": "—",
            "period_cost_usd": None,
            "period_cost_display": "—",
            "period_average_tokens": None,
            "period_average_tokens_display": "—",
            "period_count": 0,
            "active_period_count": 0,
            "period_unit": token_period_unit(group_by, language=language),
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
        parsed_date = parse_token_usage_date(raw_date)
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
                "date": parsed_date.date().isoformat() if parsed_date else format_iso_date(raw_date),
                "inputTokens": total_input_tokens,
                "totalInputTokens": total_input_tokens,
                "uncachedInputTokens": uncached_input_tokens,
                "cachedInputTokens": cached_input_tokens,
                "cacheCreationTokens": safe_int(row.get("cacheCreationTokens", 0)),
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
    if requested_start or requested_end:
        parsed_rows = [
            row for row in parsed_rows
            if not row.get("parsed_date")
            or (
                (not requested_start or row["parsed_date"].date() >= requested_start.date())
                and (not requested_end or row["parsed_date"].date() <= requested_end.date())
            )
        ]
    else:
        parsed_rows = recent_token_daily_rows(parsed_rows, window_days=window_days, now_func=now_func)

    if requested_start:
        effective_start = requested_start
    elif parsed_rows and parsed_rows[0].get("parsed_date"):
        effective_start = parsed_rows[0]["parsed_date"]
    else:
        effective_start = datetime.combine(now_func().date() - timedelta(days=window_days - 1), datetime.min.time())

    if requested_end:
        effective_end = requested_end
    elif parsed_rows and parsed_rows[-1].get("parsed_date"):
        effective_end = parsed_rows[-1]["parsed_date"]
    else:
        effective_end = datetime.combine(now_func().date(), datetime.min.time())

    display_rows = aggregate_monthly_token_rows(parsed_rows, language=language) if group_by == "month" else parsed_rows
    max_daily_tokens = max((row["totalTokens"] for row in display_rows), default=0)
    latest = display_rows[-1] if display_rows else None
    trailing = display_rows[-7:]
    seven_day_total = sum(item["totalTokens"] for item in trailing)
    seven_day_cost = sum(safe_float(item.get("costUSD")) for item in trailing)
    active_period_count = sum(1 for item in display_rows if item["totalTokens"] > 0)
    period_count = len(display_rows)
    period_total_tokens = sum(item["totalTokens"] for item in display_rows)
    period_cost = sum(safe_float(item.get("costUSD")) for item in display_rows)
    period_average_tokens = period_total_tokens // active_period_count if active_period_count else 0
    range_label = format_token_range_label(effective_start, effective_end, group_by, language=language)
    overview_note = localized(
        "{} · {} 个有数据{} · {} · {}".format(
            range_label,
            active_period_count,
            token_period_unit(group_by, language=language),
            ccusage_result.get("provider_label", "ccusage"),
            refreshed_at_display or "等待实时刷新",
        ),
        "{} · {} {} with records · {} · {}".format(
            range_label,
            active_period_count,
            token_period_unit(group_by, language=language),
            ccusage_result.get("provider_label", "ccusage"),
            refreshed_at_display or "waiting for live refresh",
        ),
        language,
    )

    today_breakdown = []
    if latest:
        total_input_tokens, cached_input_tokens, uncached_input_tokens = split_ccusage_input_tokens(latest)
        cache_creation_tokens = safe_int(latest.get("cacheCreationTokens", 0))
        cached_share = percent_of(cached_input_tokens, total_input_tokens)
        cache_creation_share = percent_of(cache_creation_tokens, latest["totalTokens"])
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
        ]
        if cache_creation_tokens > 0:
            today_breakdown.append(
                {
                    "label": localized("缓存写入", "Cache Write", language),
                    "value": cache_creation_tokens,
                    "display": compact_token(cache_creation_tokens, language=language),
                    "tone": token_breakdown_tone("cache_creation"),
                    "details": [
                        {
                            "label": localized("缓存写入", "Cache Write", language),
                            "value": cache_creation_tokens,
                            "title": localized(
                                "缓存写入：{}".format(compact_token(cache_creation_tokens, language=language)),
                                "Cache Write: {}".format(compact_token(cache_creation_tokens, language=language)),
                                language,
                            ),
                            "meta": localized(
                                "占总量 {}".format(format_percent(cache_creation_share, digits=1)),
                                "{} of total".format(format_percent(cache_creation_share, digits=1)),
                                language,
                            ),
                        },
                    ],
                    "details_heading": localized("缓存写入详情", "Cache write details", language),
                }
            )
        today_breakdown.extend(
            [
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
        )

    return {
        "available": True,
        "error": "",
        "daily_rows": [
            {
                "label": row["date_label"],
                "date": row.get("date") or row["raw_date"],
                "raw_date": row["raw_date"],
                "sort_key": row["sort_key"],
                "group_by": group_by,
                "day_count": row.get("day_count", 1),
                "active_day_count": row.get("active_day_count", 1 if row["totalTokens"] > 0 else 0),
                "value": row["totalTokens"],
                "inputTokens": row.get("inputTokens", 0),
                "totalInputTokens": row.get("totalInputTokens", row.get("inputTokens", 0)),
                "uncachedInputTokens": row.get("uncachedInputTokens", 0),
                "cachedInputTokens": row.get("cachedInputTokens", 0),
                "cacheCreationTokens": row.get("cacheCreationTokens", 0),
                "outputTokens": row.get("outputTokens", 0),
                "reasoningOutputTokens": row.get("reasoningOutputTokens", 0),
                "totalTokens": row["totalTokens"],
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
            for row in display_rows
        ],
        "today_breakdown": today_breakdown,
        "today_total_tokens": latest["totalTokens"] if latest else 0,
        "today_total_tokens_display": compact_token(latest["totalTokens"], language=language) if latest else "0",
        "seven_day_total_tokens": seven_day_total,
        "seven_day_total_tokens_display": compact_token(seven_day_total, language=language),
        "seven_day_cost_usd": seven_day_cost,
        "seven_day_cost_display": format_usd(seven_day_cost),
        "today_date_label": latest["date_label"] if latest else localized("今日", "Today", language),
        "current_period_label": latest["date_label"] if latest else range_label,
        "range_label": range_label,
        "range_start": effective_start.date().isoformat() if isinstance(effective_start, datetime) else str(effective_start or ""),
        "range_end": effective_end.date().isoformat() if isinstance(effective_end, datetime) else str(effective_end or ""),
        "group_by": group_by,
        "period_total_tokens": period_total_tokens,
        "period_total_tokens_display": compact_token(period_total_tokens, language=language),
        "period_cost_usd": period_cost,
        "period_cost_display": format_usd(period_cost),
        "period_average_tokens": period_average_tokens,
        "period_average_tokens_display": compact_token(period_average_tokens, language=language),
        "period_count": period_count,
        "active_period_count": active_period_count,
        "period_unit": token_period_unit(group_by, language=language),
        "summary_cards": build_token_summary_cards(
            display_rows,
            trailing,
            latest,
            language=language,
            group_by=group_by,
            custom_period=bool(start_date or end_date or group_by == "month"),
        ),
        "overview_note": overview_note,
        "refreshed_at": refreshed_at,
        "refreshed_at_display": refreshed_at_display,
        "window_days": window_days,
        "provider": ccusage_result.get("provider", "codex"),
        "provider_label": ccusage_result.get("provider_label", "ccusage"),
        "provider_results": ccusage_result.get("provider_results", {}),
        "partial": bool(ccusage_result.get("partial")),
    }
