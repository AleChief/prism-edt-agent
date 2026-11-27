"""Отчёт по результатам: модель × вариант — решено по прогонам, среднее, токены, вызовы, время и цена задачи.

Среднее считается только по полным и целиком оценённым прогонам. Прогон с «*» неполный или оценён не весь
(в скобках — сколько задач в нём есть). «Подмен» — ответы, в которых шлюз вернул другую модель, чем заказана.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from . import config, variants

HEAD = ["модель", "вариант", "решено по прогонам", "среднее", "задач", "вход без кэша, тыс.", "из кэша, тыс.", "выход, тыс.",
        "вызовов", "мин/задачу", "$/задачу", "сбои"]


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def collect(tasks: set[str]) -> dict:
    data: dict = defaultdict(lambda: defaultdict(list))
    for meta_path in config.results().glob("*/B*/run*/meta.json"):
        series, task, run = meta_path.parts[-4], meta_path.parts[-3], meta_path.parts[-2]
        if task not in tasks or not re.fullmatch(r"run\d+", run):
            continue
        model, _, variant = series.partition("__")
        meta, score = load(meta_path), load(meta_path.with_name("score.json"))
        wanted = (meta.get("model") or model).split("/")[-1]
        data[(model, variant or "edt16")][run].append({
            "solved": bool(score.get("solved")), "scored": bool(score),
            "inp": meta.get("input_tokens") or 0, "cr": meta.get("cache_read_tokens") or 0, "cw": meta.get("cache_write_tokens") or 0,
            "out": meta.get("output_tokens") or 0, "calls": meta.get("tool_calls") or 0, "wall": meta.get("wall_s") or 0,
            "error": bool(meta.get("is_error")), "timeout": bool(meta.get("timed_out")),
            "wrong_model": any(wanted not in k for k in (meta.get("answered_by") or {}) if k != "<synthetic>"),  # <synthetic> — служебный ответ Claude Code при ошибке
        })
    return data


def rows(tasks: list[str]) -> list[list[str]]:
    data = collect(set(tasks))
    prices = config.prices()
    order = list(variants.VARIANTS)
    out = []
    for (model, variant) in sorted(data, key=lambda mv: (mv[0], order.index(mv[1]) if mv[1] in order else 99, mv[1])):
        runs = data[(model, variant)]
        complete = [sum(x["solved"] for x in items) for _, items in sorted(runs.items())
                    if len(items) == len(tasks) and all(x["scored"] for x in items)]
        per_run = [f"{sum(x['solved'] for x in items)}" + ("" if len(items) == len(tasks) and all(x["scored"] for x in items)
                                                         else f"*({len(items)})") for _, items in sorted(runs.items())]
        flat = [x for items in runs.values() for x in items]
        n = len(flat)
        price = prices.get(model)
        cost = sum(x["inp"] * price[0] + x["cr"] * price[1] + x["cw"] * price[2] + x["out"] * price[3] for x in flat) / n / 1e6 if price else None
        bad = sum(x["error"] for x in flat), sum(x["timeout"] for x in flat), sum(x["wrong_model"] for x in flat)
        out.append([model, variants.VARIANTS.get(variant, {}).get("title", variant), ", ".join(per_run),
                    f"{sum(complete) / len(complete):.1f}" if complete else "—", str(n),
                    f"{sum(x['inp'] + x['cw'] for x in flat) / n / 1000:.1f}", f"{sum(x['cr'] for x in flat) / n / 1000:.1f}",
                    f"{sum(x['out'] for x in flat) / n / 1000:.1f}", f"{sum(x['calls'] for x in flat) / n:.1f}",
                    f"{sum(x['wall'] for x in flat) / n / 60:.1f}", f"{cost:.4f}" if cost is not None else "—",
                    "" if not any(bad) else f"ошибок {bad[0]}, по пределу {bad[1]}, подмен {bad[2]}"])
    return out


def report(tasks: list[str], csv: bool = False) -> str:
    body = rows(tasks)
    if csv:
        return "\n".join(";".join(r) for r in [HEAD, *body])
    lines = [f"Задачи: {tasks[0]}–{tasks[-1]} ({len(tasks)}). Токены — тысячи на задачу; «*» — прогон неполный или оценён не весь.", "",
             "| " + " | ".join(HEAD) + " |", "|" + "---|" * len(HEAD)]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines) if body else "результатов нет: " + str(config.results())
