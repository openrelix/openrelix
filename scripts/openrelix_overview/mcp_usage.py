"""Best-effort MCP tool usage counters for Codex session logs."""

import json
from collections import OrderedDict
from datetime import date as date_cls
from datetime import datetime, timedelta
from pathlib import Path


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
                row = tool_stats.setdefault(
                    name,
                    {
                        "name": name,
                        "server": server,
                        "tool": tool,
                        "label": "{}/{}".format(server, tool),
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
