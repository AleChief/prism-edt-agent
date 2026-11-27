"""Прогон одной задачи агентом: Claude Code без окна, инструменты — EDT MCP и/или обёртка стенда.

Ход прогона:
  1. модуль «КодКандидата» проекта задачи сбрасывается в заглушку (через EDT MCP);
  2. запускается `claude -p` с условием задачи; агент смотрит метаданные, пишет функцию, читает ошибки и исправляется;
  3. текст модуля, журнал и сводка (ходы, токены, время, вызовы инструментов) сохраняются в
     <results>/<модель>__<вариант>/<задача>/run<N>/;
  4. модуль снова сбрасывается в заглушку, чтобы решение не было видно следующим прогонам.
Оценка решения — отдельно (bench.py score).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from . import config, edt_client, variants

# Встроенные инструменты Claude Code агенту не нужны: файлов и команд у задачи нет.
BUILTIN_OFF = [
    "Bash", "PowerShell", "Read", "Edit", "Write", "NotebookEdit", "Glob", "Grep",
    "WebFetch", "WebSearch", "Agent", "Task", "TodoWrite", "Skill",
    # есть только в обычном (не --bare) режиме — запуск по подписке
    "CronCreate", "CronDelete", "CronList", "DesignSync", "EnterWorktree", "ExitWorktree", "ListAgents", "Monitor",
    "PushNotification", "RemoteTrigger", "ReportFindings", "ScheduleWakeup", "SendMessage", "TaskStop", "ToolSearch", "Workflow",
    "McpTaskCheck",
]


def edt(tool: str, **args) -> dict:
    return edt_client.call(config.mcp_url(), tool, args)


def reset_module(project: str) -> None:
    res = edt("write_module_source", projectName=project, modulePath=config.MODULE_PATH,
              source=config.STUB, mode="replace", overwrite=True)
    if edt_client.is_error(res):
        raise RuntimeError(f"не удалось сбросить модуль {project}: {json.dumps(res, ensure_ascii=False)[:400]}")


def read_module(project: str) -> str:
    return (config.workspace() / project / "src" / "CommonModules" / "КодКандидата" / "Module.bsl").read_text(encoding="utf-8-sig")


def lock_project(project: str, timeout_s: int = 3600):
    """Один прогон на проект EDT: параллельные серии одного экземпляра ждут, пока задача освободится."""
    import msvcrt

    locks = config.work() / "locks" / f"edt{config.instance_no()}"
    locks.mkdir(parents=True, exist_ok=True)
    fh = open(locks / f"{project}.lock", "a+b")
    deadline = time.time() + timeout_s
    while True:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return fh
        except OSError:
            if time.time() > deadline:
                raise SystemExit(f"проект {project} занят другим прогоном дольше {timeout_s} с")
            time.sleep(5)


def project_metadata(project: str) -> str:
    """Описание метаданных для варианта без инструментов: то же, что агент получает от inspect_project, без текста модуля."""
    cache = config.work() / "meta" / f"{project}.md"
    if not cache.exists():
        from . import mcp_server

        text = mcp_server.inspect_project(project).split("# Модуль КодКандидата сейчас")[0].strip()
        if "Metadata" not in text:
            raise SystemExit(f"{project}: описание метаданных не получено: {text[:300]}")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text, encoding="utf-8")
    return cache.read_text(encoding="utf-8")


def final_answer(lines: list[str]) -> str:
    for line in reversed(lines):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "result":
            return ev.get("result") or ""
    return ""


def extract_code(answer: str) -> str:
    """Текст модуля из ответа: самый длинный блок в тройных кавычках, а без блоков — весь ответ."""
    blocks = re.findall(r"```[^\n]*\n(.*?)```", answer, re.S)
    code = (max(blocks, key=len) if blocks else answer).strip()
    return code + "\n" if code else ""


def write_mcp_config(out: Path, project: str, v: dict) -> Path:
    servers = {}
    if v["edt_tools"]:
        servers["edt"] = {"type": "http", "url": config.mcp_url()}
    if v["bench_tools"]:
        servers["bench"] = {"command": sys.executable,
                            "args": ["-I", str(config.ROOT / "stand" / "mcp_server.py"), "--project", project,
                                     "--tools", ",".join(v["bench_tools"]), *(["--strict"] if v.get("strict") else [])],
                            "env": {"PRISM_EDT_INSTANCE": str(config.instance_no()),
                                    "PRISM_EDT_SETTINGS": str(config.settings_path())}}
    path = out / "mcp.json"
    path.write_text(json.dumps({"mcpServers": servers}, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def claude_command(model: str, mcp_config: Path, v: dict) -> list[str]:
    exe = shutil.which("claude")
    if not exe:
        raise SystemExit("claude не найден в PATH: установите Claude Code CLI")
    # подписка: --bare требует ключ API, поэтому обычный режим без пользовательских настроек, скиллов и команд
    mode = ["--setting-sources", "project", "--disable-slash-commands"] if subscription() else ["--bare"]
    allowed = [f"mcp__edt__{t}" for t in v["edt_tools"]] + [f"mcp__bench__{t}" for t in v["bench_tools"]]
    hidden = [f"mcp__edt__{t}" for t in variants.EDT16 if v["edt_tools"] and t not in v["edt_tools"]]
    cmd = [exe, "-p", *mode, "--model", model, "--mcp-config", str(mcp_config), "--strict-mcp-config",
           *(["--allowedTools", ",".join(allowed)] if allowed else []),
           "--disallowedTools", ",".join(BUILTIN_OFF + hidden),
           "--no-session-persistence", "--output-format", "stream-json", "--verbose"]
    return ["cmd", "/c", *cmd] if exe.lower().endswith((".cmd", ".bat")) else cmd


def subscription() -> bool:
    return (config.settings()["agent"].get("auth") or "api") == "subscription"


def agent_env(model: str) -> dict:
    env = dict(os.environ)
    agent = config.settings()["agent"]
    if subscription():
        for name in [n for n in env if n.startswith("ANTHROPIC_")]:
            del env[name]
        return env
    key_env = agent.get("api_key_env") or "ANTHROPIC_API_KEY"
    key = os.environ.get(key_env)
    if not key:
        raise SystemExit(f"нет ключа сервера модели: переменная окружения {key_env} пуста")
    env.update({"ANTHROPIC_API_KEY": key, "ANTHROPIC_AUTH_TOKEN": key, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
    if agent.get("base_url"):
        # шлюз со сторонними моделями: все «семейства» Claude Code указывают на выбранную модель
        env.update({"ANTHROPIC_BASE_URL": agent["base_url"], "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
                    "ANTHROPIC_DEFAULT_SONNET_MODEL": model, "ANTHROPIC_DEFAULT_OPUS_MODEL": model})
    return env


def parse_transcript(lines: list[str]) -> dict:
    """Сводка по журналу stream-json: ходы, токены, вызовы инструментов, какая модель отвечала на самом деле."""
    calls: list[tuple[str, str]] = []
    result: dict = {}
    models: Counter = Counter()  # шлюз может тихо подменить модель запасной
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "assistant":
            models[(ev.get("message") or {}).get("model") or "?"] += 1
            for block in (ev.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    calls.append((block.get("name", ""), json.dumps(block.get("input"), ensure_ascii=False, sort_keys=True)))
        elif ev.get("type") == "result":
            result = ev
    by_tool = Counter(name.replace("mcp__edt__", "").replace("mcp__bench__", "") for name, _ in calls)
    repeats = Counter(calls)
    usage = result.get("usage") or {}
    return {
        "finished": bool(result),
        "is_error": result.get("is_error"),
        "answer": (result.get("result") or "")[:300],
        "num_turns": result.get("num_turns"),
        "duration_ms": result.get("duration_ms"),
        "input_tokens": usage.get("input_tokens"),
        "cache_read_tokens": usage.get("cache_read_input_tokens"),
        "cache_write_tokens": usage.get("cache_creation_input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "tool_calls": len(calls),
        "tool_calls_by_name": dict(by_tool.most_common()),
        "max_identical_calls": max(repeats.values()) if repeats else 0,
        "answered_by": dict(models),
    }


def run_task(task_id: str, model: str, variant: str, run: int, dry_run: bool = False) -> dict:
    config.use_prism_imports()
    import yaml

    v = variants.get(variant)
    task = yaml.safe_load((config.find_task(task_id) / "task.yaml").read_text(encoding="utf-8"))
    project = f"prism_{task_id}"
    notools = v["prompt"] == "notools"
    metadata = project_metadata(project) if notools else ""
    prompt = variants.prompt(variant, project, task["prompt"].strip(), config.MODULE_PATH, metadata)

    if not notools:  # без инструментов проект EDT не трогается: блокировка и сброс не нужны
        _lock = lock_project(project) if not dry_run else None  # держится до конца процесса  # noqa: F841
        reset_module(project)
        if read_module(project).strip() != config.STUB.strip():
            raise SystemExit(f"{project}: модуль после сброса не равен заглушке")
        if v.get("groups"):  # каждая задача начинает с одной видимой группы core
            edt("enable_toolset", toolsets=variants.GROUPS, disable=True)

    out = config.results() / config.model_slug(model, variant) / task_id / f"run{run}"
    if dry_run:
        print("--- условие ---\n" + prompt)
        print(f"каталог результата: {out}")
        return {"dry_run": True}

    env = agent_env(model)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "prompt.txt").write_text(prompt, encoding="utf-8")
    cmd = claude_command(model, write_mcp_config(out, project, v), v)
    cwd = config.work() / "agent-cwd"  # пустой каталог: у агента нет файлов проекта
    cwd.mkdir(parents=True, exist_ok=True)

    started, timed_out = time.time(), False
    try:
        proc = subprocess.run(cmd, input=prompt.encode("utf-8"), capture_output=True, env=env, cwd=cwd, timeout=config.task_timeout())
        stdout, stderr, rc = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out, stdout, stderr, rc = True, exc.stdout or b"", exc.stderr or b"", None
    wall = round(time.time() - started, 1)

    text = stdout.decode("utf-8", "replace")
    (out / "transcript.jsonl").write_text(text, encoding="utf-8")
    if stderr:
        (out / "stderr.txt").write_text(stderr.decode("utf-8", "replace"), encoding="utf-8")
    code = extract_code(final_answer(text.splitlines())) if notools else read_module(project)
    (out / "candidate.bsl").write_text(code, encoding="utf-8")
    if not notools:
        reset_module(project)

    meta = {
        "task": task_id, "model": model, "variant": variant, "run": run, "edt": config.instance_no(),
        "exit_code": rc, "timed_out": timed_out, "wall_s": wall,
        "module_changed": bool(code.strip()) and code.strip() != config.STUB.strip(),
        **parse_transcript(text.splitlines()),
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta
