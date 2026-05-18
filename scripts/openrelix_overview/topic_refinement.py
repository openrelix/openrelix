"""Second-pass topic refinement rules for broad window-overview buckets."""

REFINEMENT_TRIGGER_LABELS = frozenset(
    {
        "移动端编译/类型错误",
        "代码清理与本地提交",
    }
)

REFINEMENT_RULES = (
    {
        "label": "GS 埋点 BTM",
        "all": ("btm",),
        "any": ("埋点", "上报", "headview", "tabtype"),
    },
    {
        "label": "垂搜请求参数",
        "any": (
            "enable_history",
            "request bean",
            "envtypes",
            "请求参数",
            "stream 请求",
            "上报差异",
        ),
    },
    {
        "label": "GS 筛选状态保持",
        "all": ("gs",),
        "any": (
            "选中态",
            "selected",
            "筛选刷新",
            "重绑",
            "丢失选中态",
            "bindgs",
            "guidesearchmodule",
        ),
    },
    {
        "label": "垂搜空态与 footer",
        "any": ("空态", "footer", "空文案", "emptype", "hasmore=false", "暂无更多"),
        "project_or_text_any": ("垂搜", "图文", "视频", "综搜", "experience", "image", "video"),
    },
)


def should_refine_label(label):
    text = str(label or "")
    return (
        text in REFINEMENT_TRIGGER_LABELS
        or text.startswith("其他需求")
        or text.lower().startswith("other needs")
    )


def refinement_text(item, context_text):
    return " ".join(
        [
            str(context_text or ""),
            " ".join(
                str(item.get(key, "") or "")
                for key in ("project_label", "cwd", "cwd_display", "window_title", "window_summary")
            ),
        ]
    )


def rule_matches(rule, lowered_text):
    required_terms = rule.get("all") or ()
    if any(str(term or "").lower() not in lowered_text for term in required_terms):
        return False

    any_terms = rule.get("any") or ()
    if any_terms and not any(str(term or "").lower() in lowered_text for term in any_terms):
        return False

    project_or_text_terms = rule.get("project_or_text_any") or ()
    if project_or_text_terms and not any(
        str(term or "").lower() in lowered_text for term in project_or_text_terms
    ):
        return False

    return True


def refined_label(item, initial_label="", context_text=""):
    if not should_refine_label(initial_label):
        return ""

    lowered = " ".join(refinement_text(item, context_text).split()).lower()
    if not lowered:
        return ""

    for rule in REFINEMENT_RULES:
        if rule_matches(rule, lowered):
            return rule["label"]
    return ""
