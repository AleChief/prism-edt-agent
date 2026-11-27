"""Проверка готовности стенда: настройки, PRISM, платформа, Claude Code, ключ модели, EDT MCP, проекты задач."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from . import config, edt_client


def _item(ok: bool | None, name: str, detail: str = "") -> bool:
    mark = {True: "ок ", False: "НЕТ", None: "—  "}[ok]
    print(f"[{mark}] {name}" + (f": {detail}" if detail else ""))
    return ok is not False


def check(instances: list[int] | None = None) -> int:
    good = True
    try:
        config.settings()
    except SystemExit as exc:
        _item(False, "settings.yaml", str(exc))
        return 1
    good &= _item(True, "settings.yaml", str(config.settings_path()))
    good &= _item(sys.version_info >= (3, 10), "Python 3.10+", sys.version.split()[0])

    prism = config.prism()
    has_prism = (prism / "harness" / "execute" / "onec" / "assemble.py").exists()
    good &= _item(has_prism, "PRISM", str(prism))
    if has_prism:
        config.use_prism_imports()
        try:
            import yaml  # noqa: F401
            from harness.synthconfig import build  # noqa: F401

            n = len(config.all_tasks())
            good &= _item(n > 0, "задачи категории B", f"{n} (PRISM + tasks/ стенда)")
        except ImportError as exc:
            good &= _item(False, "зависимости PRISM", f"{exc} — установите PRISM: pip install -e {prism}")

    bin_ = config.platform_bin()
    missing = [f for f in ("ibcmd.exe", "1cv8.exe", "1cv8c.exe") if not (bin_ / f).exists()]
    good &= _item(not missing, "платформа 1С", str(bin_) + (f", нет {', '.join(missing)}" if missing else ""))

    claude = shutil.which("claude")
    if claude:
        ver = subprocess.run(["cmd", "/c", claude, "--version"] if claude.lower().endswith((".cmd", ".bat")) else [claude, "--version"],
                             capture_output=True, text=True, errors="replace").stdout.strip()
        good &= _item(True, "Claude Code CLI", ver)
    else:
        good &= _item(False, "Claude Code CLI", "команда claude не найдена в PATH")

    agent = config.settings()["agent"]
    if (agent.get("auth") or "api") == "subscription":
        _item(None, "ключ модели", "auth: subscription — вход по подписке Claude Code (claude login)")
    else:
        env = agent.get("api_key_env") or "ANTHROPIC_API_KEY"
        good &= _item(bool(os.environ.get(env)), "ключ модели", f"переменная {env}" + ("" if os.environ.get(env) else " пуста"))
        _item(None, "сервер модели", agent.get("base_url") or "api.anthropic.com")

    edt = config.settings()["edt"]
    for key in ("exe", "javaw"):
        p = config._path(edt[key])
        good &= _item(p.exists(), f"EDT: {key}", str(p))

    work = config.work()
    _item((work / "empty-cfg-ru" / "Configuration.xml").exists() or None, "каркас конфигурации",
          str(work / "empty-cfg-ru") + ("" if (work / "empty-cfg-ru").exists() else " — будет создан bench.py prepare"))

    for n in instances or sorted(int(k) for k in (edt.get("instances") or {})):
        os.environ["PRISM_EDT_INSTANCE"] = str(n)
        try:
            url = config.mcp_url()
            tools = edt_client.list_tools(url, timeout=10)
            good &= _item(True, f"EDT {n}: сервер MCP", f"{url}, инструментов {len(tools)}")
        except SystemExit as exc:
            good &= _item(False, f"EDT {n}", str(exc))
            continue
        except Exception as exc:  # noqa: BLE001
            _item(None, f"EDT {n}: сервер MCP", f"{url} не отвечает ({type(exc).__name__}) — EDT не запущена? bench.py --edt {n} mode 16")
            continue
        projects = [t for t in config.all_tasks() if (config.workspace() / f"prism_{t}").exists()]
        good &= _item(len(projects) == len(config.all_tasks()), f"EDT {n}: проекты задач",
                      f"{len(projects)} из {len(config.all_tasks())}" + ("" if len(projects) == len(config.all_tasks()) else " — bench.py prepare"))
    print("\nстенд готов" if good else "\nесть проблемы — см. строки «НЕТ»")
    return 0 if good else 1
