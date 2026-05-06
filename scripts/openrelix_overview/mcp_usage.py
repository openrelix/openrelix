"""Best-effort MCP tool usage counters for Codex session logs."""

import json
from collections import OrderedDict
from datetime import date as date_cls
from datetime import datetime, timedelta
from pathlib import Path


MCP_SERVER_DESCRIPTIONS = {
    "playwright": (
        "浏览器自动化工具，用于打开页面、读取页面结构、截图、点击、输入和检查网络请求。",
        "Browser automation tools for opening pages, reading page structure, screenshots, clicks, typing, and network checks.",
    ),
    "openaiDeveloperDocs": (
        "OpenAI 官方开发者文档工具，用于搜索文档、读取指定页面和查询 API endpoint 规范。",
        "Official OpenAI developer documentation tools for searching docs, fetching pages, and reading API endpoint specs.",
    ),
    "figma": (
        "Figma 设计协作工具，用于读取设计上下文、截图、变量、组件信息，或把结果写回 Figma。",
        "Figma design-collaboration tools for design context, screenshots, variables, components, and writing back to Figma.",
    ),
    "node_repl": (
        "Node.js 临时执行环境，用于快速验证 JavaScript、前端数据处理或小段逻辑。",
        "A temporary Node.js execution environment for quick JavaScript, frontend data, or small logic checks.",
    ),
}

MCP_TOOL_DESCRIPTIONS = {
    ("playwright", "browser_navigate"): (
        "在本地浏览器打开或跳转到指定 URL，常用于验证本地页面和外部网页。",
        "Navigate the local browser to a URL, often for validating local pages or external sites.",
    ),
    ("playwright", "browser_resize"): (
        "调整本地浏览器视口尺寸，用于检查桌面、平板或手机布局。",
        "Resize the local browser viewport to check desktop, tablet, or mobile layouts.",
    ),
    ("playwright", "browser_tabs"): (
        "创建、切换、关闭或列出浏览器标签页。",
        "Create, switch, close, or list browser tabs.",
    ),
    ("playwright", "browser_run_code"): (
        "运行一段 Playwright 代码片段，处理更复杂的页面检查或交互。",
        "Run a Playwright code snippet for more complex page checks or interactions.",
    ),
    ("playwright", "browser_snapshot"): (
        "读取当前页面的可访问性快照，比截图更适合定位按钮、表单和文本。",
        "Read the current page accessibility snapshot, which is better than screenshots for locating controls and text.",
    ),
    ("playwright", "browser_take_screenshot"): (
        "截取当前页面或指定元素，用于 UI 验证和视觉回归检查。",
        "Capture the current page or an element for UI validation and visual checks.",
    ),
    ("playwright", "browser_click"): (
        "点击页面上的按钮、链接或其他可交互元素。",
        "Click a button, link, or other interactive page element.",
    ),
    ("playwright", "browser_type"): (
        "向页面输入框或可编辑区域输入文本。",
        "Type text into page inputs or editable areas.",
    ),
    ("playwright", "browser_fill_form"): (
        "批量填写表单控件，包括文本框、复选框、单选项和下拉框。",
        "Fill form controls in bulk, including text fields, checkboxes, radios, and selects.",
    ),
    ("playwright", "browser_press_key"): (
        "向页面发送键盘按键，例如 Enter、Escape 或方向键。",
        "Send keyboard keys to the page, such as Enter, Escape, or arrow keys.",
    ),
    ("playwright", "browser_wait_for"): (
        "等待页面文本出现、消失，或等待指定时间。",
        "Wait for page text to appear or disappear, or wait for a fixed time.",
    ),
    ("playwright", "browser_evaluate"): (
        "在页面上下文执行 JavaScript，用于读取状态或做轻量 DOM 检查。",
        "Run JavaScript in the page context to inspect state or perform lightweight DOM checks.",
    ),
    ("playwright", "browser_console_messages"): (
        "读取浏览器控制台消息，用于排查前端错误和警告。",
        "Read browser console messages for frontend errors and warnings.",
    ),
    ("playwright", "browser_network_requests"): (
        "列出页面网络请求，用于排查接口、资源加载和失败请求。",
        "List page network requests to inspect APIs, resource loading, and failed requests.",
    ),
    ("playwright", "browser_network_request"): (
        "查看单个网络请求的请求头、请求体、响应头或响应体。",
        "Inspect headers or body for a single network request or response.",
    ),
    ("openaiDeveloperDocs", "search_openai_docs"): (
        "搜索 OpenAI 官方开发者文档，适合查找 API、SDK、模型和产品说明。",
        "Search official OpenAI developer docs for APIs, SDKs, models, and product guidance.",
    ),
    ("openaiDeveloperDocs", "fetch_openai_doc"): (
        "读取指定 OpenAI 文档页面或锚点，获取可引用的最新说明。",
        "Fetch a specific OpenAI doc page or anchor for current, citable guidance.",
    ),
    ("openaiDeveloperDocs", "get_openapi_spec"): (
        "读取指定 API endpoint 的 OpenAPI 规范和示例代码。",
        "Read the OpenAPI spec and code examples for a specific API endpoint.",
    ),
    ("openaiDeveloperDocs", "list_openai_docs"): (
        "浏览当前可检索的 OpenAI 文档页面列表。",
        "Browse the list of currently indexed OpenAI documentation pages.",
    ),
    ("openaiDeveloperDocs", "list_api_endpoints"): (
        "列出 OpenAI OpenAPI 规范中可用的 API endpoint。",
        "List API endpoints available in the OpenAI OpenAPI specification.",
    ),
    ("figma", "get_design_context"): (
        "读取 Figma 节点的设计上下文、截图和参考代码，用于设计还原。",
        "Fetch design context, screenshot, and reference code for a Figma node.",
    ),
    ("figma", "get_screenshot"): (
        "导出 Figma 节点截图，用于视觉对照和实现验证。",
        "Export a Figma node screenshot for visual comparison and implementation checks.",
    ),
    ("figma", "search_design_system"): (
        "搜索 Figma 设计系统中的组件、变量和样式。",
        "Search Figma design-system components, variables, and styles.",
    ),
    ("figma", "use_figma"): (
        "通过 Figma Plugin API 创建、修改或检查 Figma 文件内容。",
        "Create, modify, or inspect Figma file content through the Figma Plugin API.",
    ),
    ("node_repl", "js"): (
        "执行一段 JavaScript，用于快速验证数据转换、表达式或浏览器外逻辑。",
        "Execute JavaScript to quickly validate data transforms, expressions, or non-browser logic.",
    ),
}


def _humanize_identifier(value):
    text = str(value or "").replace("_", " ").replace("-", " ").strip()
    return " ".join(part for part in text.split() if part)


def describe_mcp_tool(server, tool):
    """Return localized descriptions for a sanitized MCP server/tool pair."""
    server = str(server or "").strip()
    tool = str(tool or "").strip()
    direct = MCP_TOOL_DESCRIPTIONS.get((server, tool))
    if direct:
        return direct
    server_description = MCP_SERVER_DESCRIPTIONS.get(server)
    tool_label = _humanize_identifier(tool) or "tool"
    server_label = _humanize_identifier(server) or "MCP"
    if server_description:
        return (
            "调用 {} 的 {} 工具。{}".format(server_label, tool_label, server_description[0]),
            "Calls the {} tool from {}. {}".format(tool_label, server_label, server_description[1]),
        )
    return (
        "来自 {} MCP 服务的 {} 工具；OpenRelix 只记录工具名和聚合次数。".format(server_label, tool_label),
        "A {} tool from the {} MCP server; OpenRelix only records the tool name and aggregate counts.".format(
            tool_label,
            server_label,
        ),
    )


def _coerce_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date_cls):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip()).date()
        except ValueError:
            pass
    return datetime.now().date()


def _parse_json_line(line):
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _parse_arguments(value):
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_mcp_call_name(raw_name):
    """Return (server, tool, normalized_name) for an MCP function call name."""
    name = str(raw_name or "").strip()
    if name.startswith("functions."):
        name = name.split(".", 1)[1]
    parts = name.split("__", 2)
    if len(parts) != 3 or parts[0] != "mcp" or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2], name


def _iter_codex_sessions_for_date(paths, session_date):
    root = paths.codex_home / "sessions" / session_date.strftime("%Y") / session_date.strftime("%m") / session_date.strftime("%d")
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for path in sorted(entries):
        if path.name.startswith("rollout-") and path.name.endswith(".jsonl") and path.is_file():
            yield path


def _iter_mcp_calls_from_payload(payload):
    if not isinstance(payload, dict) or payload.get("type") != "function_call":
        return

    direct = parse_mcp_call_name(payload.get("name"))
    if direct:
        yield direct
        return

    if payload.get("name") != "multi_tool_use.parallel":
        return
    args = _parse_arguments(payload.get("arguments"))
    for tool_use in args.get("tool_uses") or []:
        if not isinstance(tool_use, dict):
            continue
        nested = parse_mcp_call_name(tool_use.get("recipient_name"))
        if nested:
            yield nested


def _scan_codex_session(path):
    try:
        handle = Path(path).open(encoding="utf-8")
    except OSError:
        return []
    calls = []
    with handle:
        for line in handle:
            row = _parse_json_line(line)
            if not isinstance(row, dict) or row.get("type") != "response_item":
                continue
            payload = row.get("payload")
            for call in _iter_mcp_calls_from_payload(payload):
                calls.append(call)
    return calls


def build_mcp_usage_view(paths, today, lookback_days=30, limit=10):
    """Build a sanitized MCP usage summary from recent Codex function calls."""
    anchor = _coerce_date(today)
    days = max(int(lookback_days or 0), 1)
    start = anchor - timedelta(days=days - 1)
    tool_stats = OrderedDict()
    server_stats = OrderedDict()
    scanned_sessions = 0

    for offset in range(days):
        session_date = start + timedelta(days=offset)
        for session_path in _iter_codex_sessions_for_date(paths, session_date):
            scanned_sessions += 1
            session_tool_names = set()
            session_server_names = set()
            for server, tool, name in _scan_codex_session(session_path):
                description, description_en = describe_mcp_tool(server, tool)
                row = tool_stats.setdefault(
                    name,
                    {
                        "name": name,
                        "server": server,
                        "tool": tool,
                        "label": "{}/{}".format(server, tool),
                        "description": description,
                        "description_en": description_en,
                        "calls": 0,
                        "sessions": 0,
                        "last_seen": "",
                    },
                )
                row["calls"] += 1
                if not row["last_seen"] or session_date.isoformat() > row["last_seen"]:
                    row["last_seen"] = session_date.isoformat()
                session_tool_names.add(name)

                server_row = server_stats.setdefault(
                    server,
                    {
                        "server": server,
                        "label": server,
                        "calls": 0,
                        "sessions": 0,
                        "last_seen": "",
                    },
                )
                server_row["calls"] += 1
                if not server_row["last_seen"] or session_date.isoformat() > server_row["last_seen"]:
                    server_row["last_seen"] = session_date.isoformat()
                session_server_names.add(server)

            for name in session_tool_names:
                tool_stats[name]["sessions"] += 1
            for server in session_server_names:
                server_stats[server]["sessions"] += 1

    def sort_key(row):
        return (-int(row.get("calls") or 0), -int(row.get("sessions") or 0), str(row.get("label") or ""))

    tools = sorted(tool_stats.values(), key=sort_key)
    servers = sorted(server_stats.values(), key=sort_key)
    if limit is not None:
        capped = max(int(limit or 0), 0)
        tools = tools[:capped]
        servers = servers[:capped]

    return {
        "lookback_days": days,
        "start_date": start.isoformat(),
        "end_date": anchor.isoformat(),
        "scanned_sessions": scanned_sessions,
        "total_calls": sum(int(row.get("calls") or 0) for row in tool_stats.values()),
        "active_tools": len(tool_stats),
        "active_servers": len(server_stats),
        "tools": tools,
        "servers": servers,
    }
