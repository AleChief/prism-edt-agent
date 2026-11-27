"""Серия: модели × задачи × прогоны одного варианта, затем оценка решений.

Задачи идут по очереди. Готовые прогоны (есть meta.json) пропускаются, поэтому серию можно прервать и продолжить
той же командой. Сбой не по вине модели (нет итога, ошибка сессии, код не записан) повторяется один раз.
Оценка идёт после каждого прогона модели целиком; если мест лицензии нет, оценщик ждёт.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import config, scorer, variants

RETRY_PAUSE_S = 90
LICENSE_WAIT_S = 60
LICENSE_TRIES = 30


def log(msg: str) -> None:
    line = f"{datetime.now():%H:%M:%S} {msg}"
    print(line, flush=True)
    config.results().mkdir(parents=True, exist_ok=True)
    with (config.results() / "series.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def last_json(text: str) -> dict:
    for line in reversed(text.strip().splitlines()):
        if line.strip().startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {}


def run_agent(task: str, model: str, variant: str, run: int) -> dict:
    """Прогон в отдельном процессе: зависший агент не роняет серию."""
    timeout = config.task_timeout() + 180 + 1800  # предел задачи + сброс модуля + ожидание блокировки проекта
    cmd = [sys.executable, "-I", str(config.ROOT / "bench.py"), "--edt", str(config.instance_no()),
           "agent", "--task", task, "--model", model, "--variant", variant, "--run", str(run)]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"series_error": "прогон не завершился за отведённое время"}
    meta = last_json(res.stdout.decode("utf-8", "replace"))
    if not meta:
        tail = (res.stderr or res.stdout).decode("utf-8", "replace")[-300:]
        return {"series_error": f"прогон без итога, код {res.returncode}: {tail}"}
    return meta


def infra_failure(meta: dict) -> bool:
    if meta.get("series_error"):
        return True
    return (not meta.get("finished") or meta.get("is_error")) and not meta.get("module_changed")


def score_run(run_dir: Path, task: str) -> dict:
    tag = "-".join(run_dir.relative_to(config.results()).parts)
    code = (run_dir / "candidate.bsl").read_text(encoding="utf-8-sig")
    for attempt in range(LICENSE_TRIES):
        score = scorer.score(task, code, tag)
        # клиент, не получивший лицензию у занятого чужими сеансами ключа, выходит за секунду с кодом 1 без result.txt
        no_seat = score.get("status") == "no_result" and "с кодом 1" in str(score.get("infra_detail"))
        if score.get("status") != "license_busy" and not no_seat:
            return score
        log(f"   оценка {tag}: мест лицензии нет, жду {LICENSE_WAIT_S} с (попытка {attempt + 1})")
        time.sleep(LICENSE_WAIT_S)
    return {}


def score_series(model: str, variant: str, tasks: list[str], run: int) -> None:
    for task in tasks:
        run_dir = config.results() / config.model_slug(model, variant) / task / f"run{run}"
        if not (run_dir / "candidate.bsl").exists() or (run_dir / "score.json").exists():
            continue
        score = score_run(run_dir, task)
        if not score:
            log(f"   {task}: оценка отложена — лицензия занята")
            continue
        (run_dir / "score.json").write_text(json.dumps(score, ensure_ascii=False), encoding="utf-8")
        verdict = "решено" if score.get("solved") else f"{score.get('passed', 0)}/{score.get('total', 0)} {score.get('status')}"
        log(f"   {task}: {verdict} {(score.get('log') or '; '.join(score.get('compile_errors') or []))[:140]}")


def done(run_dir) -> bool:
    """Прогон готов, если есть meta.json и он не ошибка агента или шлюза (такие при повторном запуске снимаются заново)."""
    meta_path = run_dir / "meta.json"
    if not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except ValueError:
        return False
    return not meta.get("is_error") and not meta.get("series_error")


def run_series(models: list[str], variant: str, tasks: list[str], runs: list[int], score_only: bool = False) -> None:
    variants.get(variant)
    log(f"серия: модели {models}, вариант {variant}, задач {len(tasks)}, прогоны {runs}, EDT {config.instance_no()}")
    for run in runs:
        for model in models:
            if not score_only:
                log(f"== {model} {variant} прогон {run}")
                for task in tasks:
                    run_dir = config.results() / config.model_slug(model, variant) / task / f"run{run}"
                    if done(run_dir):
                        continue
                    meta = run_agent(task, model, variant, run)
                    if infra_failure(meta):
                        log(f"{task}: сбой прогона ({meta.get('series_error') or meta.get('answer') or 'нет итога'}), повтор через {RETRY_PAUSE_S} с")
                        time.sleep(RETRY_PAUSE_S)
                        meta = run_agent(task, model, variant, run)
                    if meta.get("series_error"):
                        log(f"{task}: НЕ ВЫПОЛНЕН — {meta['series_error']}")
                        continue
                    state = "таймаут" if meta.get("timed_out") else ("ошибка" if meta.get("is_error") else
                                                                     ("нет кода" if not meta.get("module_changed") else "ок"))
                    log(f"{task}: {state}, {meta.get('wall_s', 0) / 60:.1f} мин, ходов {meta.get('num_turns')}, вызовов {meta.get('tool_calls')}")
            log(f"== оценка {model} {variant} прогон {run}")
            score_series(model, variant, tasks, run)
    log("серия закончена")
