"""Клиент EDT MCP (Streamable HTTP): вызов одного инструмента и список инструментов сервера."""

from __future__ import annotations

import json
import urllib.request

HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


def _post(url: str, body: dict, session: str | None = None, timeout: int = 3600):
    headers = dict(HEADERS)
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(url, json.dumps(body).encode(), headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        sid = r.headers.get("Mcp-Session-Id") or session
        raw = r.read().decode("utf-8", "replace")
    if raw.lstrip().startswith("{"):
        return sid, json.loads(raw) if raw.strip() else None
    data = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")]
    return sid, json.loads(data[-1]) if data else None


def _close(url: str, session: str | None) -> None:
    """EDT MCP держит каждую сессию открытой и после 10000 перестаёт принимать новые, поэтому сессия закрывается явно."""
    if not session:
        return
    try:
        urllib.request.urlopen(urllib.request.Request(url, method="DELETE", headers={"Mcp-Session-Id": session}), timeout=10).close()
    except OSError:
        pass


def _session(url: str, method: str, params: dict | None, timeout: int = 3600):
    sid, _ = _post(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "prism-edt-agent", "version": "1"}}}, timeout=timeout)
    try:
        _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid, timeout)
        _, res = _post(url, {"jsonrpc": "2.0", "id": 2, "method": method, **({"params": params} if params is not None else {})}, sid, timeout)
        return res
    finally:
        _close(url, sid)


def call(url: str, tool: str, args: dict | None = None, timeout: int = 3600) -> dict:
    """Сырой ответ JSON-RPC на tools/call."""
    return _session(url, "tools/call", {"name": tool, "arguments": args or {}}, timeout) or {}


def text(url: str, tool: str, **args) -> str:
    """Ответ инструмента одной строкой: structuredContent, если есть, иначе текстовые части."""
    res = call(url, tool, args)
    result = res.get("result") or {}
    if result.get("structuredContent") is not None:
        return json.dumps(result["structuredContent"], ensure_ascii=False)
    parts = result.get("content") or []
    joined = "\n".join((p.get("resource") or {}).get("text") or p.get("text") or "" for p in parts)
    return joined or json.dumps(res, ensure_ascii=False)[:2000]


def list_tools(url: str, timeout: int = 30) -> list[str]:
    res = _session(url, "tools/list", None, timeout) or {}
    return sorted(t["name"] for t in (res.get("result") or {}).get("tools", []))


def is_error(res: dict) -> bool:
    return bool((res.get("result") or {}).get("isError")) or "error" in res
