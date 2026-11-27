"""Переключение набора инструментов сервера EDT MCP одного экземпляра EDT стенда.

Набор задаётся в настройках плагина (mcpDisabledTools в рабочей области). Файл настроек правится только при закрытой EDT:
открытая EDT при выходе перезаписывает его своими значениями. Поэтому EDT закрывается, настройки меняются, EDT
запускается снова, и скрипт ждёт, пока сервер отдаст ожидаемое число инструментов.
"""

from __future__ import annotations

import subprocess
import time

from . import config, edt_client, variants

PREFS = ".metadata/.plugins/org.eclipse.core.runtime/.settings/com.ditrix.edt.mcp.server.prefs"


def ps(script: str) -> str:
    return subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout.strip()


def _ws_filter() -> str:
    # имя рабочей области целиком: prism-bench не должна совпадать с prism-bench2
    name = config.workspace().name.replace("'", "''")
    return ("Get-CimInstance Win32_Process -Filter \"Name='1cedt.exe'\" | Where-Object { $_.CommandLine -match "
            "([regex]::Escape('" + name + "') + '(?![\\w-])') }")


def edt_running() -> bool:
    return ps("[bool](" + _ws_filter() + ")") == "True"


def close_edt() -> None:
    if not edt_running():
        return
    ps("$e = " + _ws_filter() + "; Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $e.ProcessId } | "
       "ForEach-Object { $p = Get-Process -Id $_.ProcessId; if ($p.MainWindowHandle -ne 0) { [void]$p.CloseMainWindow() } }")
    for _ in range(40):
        time.sleep(3)
        if not edt_running():
            return
    raise SystemExit("EDT стенда не закрылась за 2 минуты — настройки не тронуты")


def start_edt() -> None:
    edt = config.settings()["edt"]
    ini = ["--launcher.ini", str(config._path(edt["launcher_ini"]))] if edt.get("launcher_ini") else []
    subprocess.Popen([str(config._path(edt["exe"])), *ini, "-data", str(config.workspace()), "-vm", str(config._path(edt["javaw"]))])


def wait_server(timeout_s: int = 600) -> list[str]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            return edt_client.list_tools(config.mcp_url())
        except Exception:
            time.sleep(5)
    raise SystemExit(f"сервер EDT MCP {config.mcp_url()} не поднялся за {timeout_s} с")


def status() -> str:
    names = edt_client.list_tools(config.mcp_url())
    modes = [m for m, n in variants.SERVER_MODE_EXPECTED.items() if n == len(names)]
    return f"EDT {config.instance_no()} ({config.mcp_url()}): сервер отдаёт инструментов: {len(names)}" + \
        (f" — похоже на набор {modes[0]}" if modes else "")


def current_mode() -> str | None:
    try:
        n = len(edt_client.list_tools(config.mcp_url(), timeout=10))
    except Exception:
        return None
    return next((m for m, c in variants.SERVER_MODE_EXPECTED.items() if c == n), None)


def switch(mode: str) -> None:
    if mode not in variants.SERVER_MODES:
        raise SystemExit(f"неизвестный набор {mode}: {', '.join(variants.SERVER_MODES)}")
    prefs = config.workspace() / PREFS
    if not prefs.exists():
        raise SystemExit(f"нет настроек плагина EDT MCP: {prefs} (рабочая область не создана? см. bench.py prepare)")
    close_edt()
    disabled = sorted(set(variants.ALL_TOOLS) - variants.SERVER_MODES[mode])
    lines = [ln for ln in prefs.read_text(encoding="utf-8").splitlines()
             if not ln.startswith(("mcpDisabledTools=", "mcpProgressiveDisclosure="))]
    lines.append("mcpDisabledTools=" + ",".join(disabled))
    if mode == "groups":
        lines.append("mcpProgressiveDisclosure=true")
    prefs.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    start_edt()
    names = wait_server()
    expected = variants.SERVER_MODE_EXPECTED[mode]
    print(f"EDT {config.instance_no()}: набор {mode}, сервер отдаёт {len(names)} (ожидалось {expected})")
    if len(names) != expected:
        raise SystemExit("число инструментов не совпало: проверьте версию плагина EDT MCP (стенд рассчитан на 2.22.1)")
    # сервер MCP отвечает раньше, чем EDT откроет проекты: первая задача ловит «EDT project whose context is not started».
    # list_projects в наборе 16 выключен, поэтому просто ждём (edt.start_delay_s, по умолчанию 180 с)
    delay = int(config.settings()["edt"].get("start_delay_s", 180))
    print(f"EDT {config.instance_no()}: жду {delay} с, пока откроются проекты")
    time.sleep(delay)
