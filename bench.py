"""Агентный стенд PRISM категории B через 1С:EDT и EDT MCP.

  python bench.py check                          проверка готовности
  python bench.py prepare [--tasks B1-B29]       исходники проектов и рабочая область EDT с проектами задач
  python bench.py variants                       варианты теста
  python bench.py mode <16|full|set30|groups|status>   набор инструментов сервера EDT MCP
  python bench.py run [--models ...] [--variant ...] [--tasks ...] [--runs 1,2,3]   серия с оценкой
  python bench.py score --task B1 (--canonical | --code файл.bsl)                    оценка одного решения
  python bench.py report [--tasks B21-B29] [--csv] [--out файл.md]                   отчёт по результатам

Общие ключи (до команды): --settings файл.yaml, --edt N (экземпляр EDT из edt.instances).
Параметры run по умолчанию берутся из раздела run в settings.yaml.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # запуск с -I не добавляет каталог скрипта


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Агентный стенд PRISM категории B через 1С:EDT и EDT MCP")
    ap.add_argument("--settings", help="файл настроек (по умолчанию settings.yaml рядом с bench.py)")
    ap.add_argument("--edt", type=int, help="номер экземпляра EDT (edt.instances)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="проверка готовности")
    p = sub.add_parser("prepare", help="подготовка проектов задач")
    p.add_argument("--tasks", help="по умолчанию все: B1–B20 из PRISM и задачи стенда")
    p.add_argument("--no-edt", action="store_true", help="только исходники, без EDT")
    sub.add_parser("variants", help="варианты теста")
    p = sub.add_parser("mode", help="набор инструментов сервера EDT MCP")
    p.add_argument("mode", choices=["16", "full", "set30", "groups", "status"])
    p = sub.add_parser("run", help="серия прогонов с оценкой")
    p.add_argument("--models", help="модели через запятую")
    p.add_argument("--variant")
    p.add_argument("--tasks")
    p.add_argument("--runs", help="номера прогонов через запятую, например 1,2,3")
    p.add_argument("--score-only", action="store_true", help="только оценить готовые прогоны")
    p.add_argument("--no-mode-switch", action="store_true", help="не переключать набор инструментов EDT под вариант")
    p = sub.add_parser("agent", help="один прогон одной задачи (без оценки)")
    p.add_argument("--task", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--variant", default="composerun")
    p.add_argument("--run", type=int, default=1)
    p.add_argument("--dry-run", action="store_true", help="сбросить модуль и показать условие, без вызова модели")
    p = sub.add_parser("score", help="оценка одного решения")
    p.add_argument("--task", required=True)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--code", help="файл с модулем решения")
    src.add_argument("--canonical", action="store_true", help="эталон задачи (самопроверка стенда)")
    p.add_argument("--tag", default="manual")
    p = sub.add_parser("report", help="отчёт по результатам")
    p.add_argument("--tasks", default="B1-B20")
    p.add_argument("--csv", action="store_true")
    p.add_argument("--out", help="записать в файл")
    args = ap.parse_args()

    if args.settings:
        os.environ["PRISM_EDT_SETTINGS"] = str(Path(args.settings).resolve())
    from stand import config

    defaults = config.settings().get("run") or {}
    # экземпляр EDT наследуют дочерние процессы: прогоны серии и MCP-сервер обёртки
    os.environ["PRISM_EDT_INSTANCE"] = str(args.edt or defaults.get("edt") or 1)

    if args.cmd == "check":
        from stand.check import check

        return check([args.edt] if args.edt else None)
    if args.cmd == "prepare":
        from stand.prepare import prepare

        return prepare(config.parse_tasks(args.tasks) if args.tasks else None, args.no_edt)
    if args.cmd == "variants":
        from stand import variants

        for name, v in variants.VARIANTS.items():
            tools = len(v["edt_tools"]) + len(v["bench_tools"])
            print(f"{name:12} {v['title']:28} набор EDT: {v['edt_mode'] or '—':6} инструментов у агента: {tools}")
        return 0
    if args.cmd == "mode":
        from stand import edt_mode

        if args.mode == "status":
            print(edt_mode.status())
        else:
            edt_mode.switch(args.mode)
        return 0
    if args.cmd == "agent":
        from stand.agent import run_task

        print(json.dumps(run_task(args.task, args.model, args.variant, args.run, args.dry_run), ensure_ascii=False))
        return 0
    if args.cmd == "score":
        from stand import scorer

        code = (config.find_task(args.task) / "canonical.bsl" if args.canonical else Path(args.code)).read_text(encoding="utf-8-sig")
        print(json.dumps(scorer.score(args.task, code, "canonical" if args.canonical and args.tag == "manual" else args.tag),
                         ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "run":
        from stand import edt_mode, variants
        from stand.series import run_series

        models = (args.models.split(",") if args.models else defaults.get("models")) or []
        variant = args.variant or defaults.get("variant") or "composerun"
        tasks = config.parse_tasks(args.tasks or defaults.get("tasks") or "B1-B20")
        runs = [int(r) for r in (args.runs.split(",") if args.runs else defaults.get("runs") or [1])]
        if not models:
            raise SystemExit("не заданы модели: --models или run.models в settings.yaml")
        need = variants.get(variant)["edt_mode"]
        if need and not args.score_only and not args.no_mode_switch and edt_mode.current_mode() != need:
            print(f"EDT {config.instance_no()}: включаю набор {need} для варианта {variant}")
            edt_mode.switch(need)
        run_series([m.strip() for m in models if m.strip()], variant, tasks, runs, args.score_only)
        from stand.report import report

        print("\n" + report(tasks))
        return 0
    if args.cmd == "report":
        from stand.report import report

        text = report(config.parse_tasks(args.tasks), args.csv)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
            print(f"записано: {args.out}")
        else:
            print(text)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
