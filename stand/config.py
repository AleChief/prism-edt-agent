"""Настройки стенда из settings.yaml и производные пути.

Файл ищется так: ключ --settings → переменная PRISM_EDT_SETTINGS → settings.yaml в корне репозитория.
Номер экземпляра EDT выбирается ключом --edt или переменной PRISM_EDT_INSTANCE (её выставляет bench.py,
чтобы MCP-серверы стенда, запущенные агентом, смотрели в тот же экземпляр).
"""

from __future__ import annotations

import os
import re
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "tasks"  # задачи стенда (B21 и далее)
MODULE_PATH = "CommonModules/КодКандидата/Module.bsl"
STUB = (
    "// Решение задачи пишется в этот модуль.\n"
    "// Модуль серверный: функция решения должна быть экспортной.\n"
)


class SettingsError(SystemExit):
    pass


def settings_path() -> Path:
    return Path(os.environ.get("PRISM_EDT_SETTINGS") or ROOT / "settings.yaml")


@lru_cache(maxsize=1)
def settings() -> dict:
    path = settings_path()
    if not path.exists():
        raise SettingsError(f"нет файла настроек {path}: скопируйте settings.example.yaml в settings.yaml и поправьте пути")
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for section in ("paths", "platform", "edt", "agent"):
        if not isinstance(data.get(section), dict):
            raise SettingsError(f"{path}: нет раздела {section}")
    return data


def _path(value: str) -> Path:
    p = Path(os.path.expandvars(str(value))).expanduser()
    return p if p.is_absolute() else (ROOT / p).resolve()


def prism() -> Path:
    return _path(settings()["paths"]["prism"])


def work() -> Path:
    return _path(settings()["paths"].get("work") or "./work")


def results() -> Path:
    return _path(settings()["paths"].get("results") or "./results")


def platform_bin() -> Path:
    return _path(settings()["platform"]["bin"])


def license_slots() -> int:
    return max(1, int(settings()["platform"].get("license_slots") or 1))


def license_limit() -> int:
    return int(settings()["platform"].get("license_limit") or 5)


def instance_no() -> int:
    return int(os.environ.get("PRISM_EDT_INSTANCE") or 1)


def instance(n: int | None = None) -> dict:
    n = instance_no() if n is None else n
    inst = (settings()["edt"].get("instances") or {}).get(n) or (settings()["edt"].get("instances") or {}).get(str(n))
    if not inst:
        raise SettingsError(f"в settings.yaml нет экземпляра EDT {n} (edt.instances)")
    return {"workspace": _path(inst["workspace"]), "port": int(inst["port"])}


def workspace() -> Path:
    return instance()["workspace"]


def mcp_url() -> str:
    return f"http://127.0.0.1:{instance()['port']}/mcp"


def task_timeout() -> int:
    return int(settings()["agent"].get("task_timeout") or 900)


def prices() -> dict:
    return {k: tuple(v) for k, v in (settings().get("prices") or {}).items()}


def use_prism_imports() -> None:
    """Модули PRISM (harness, yaml) — из клона по пути из настроек."""
    p = str(prism())
    if p not in sys.path:
        sys.path.insert(0, p)


def find_task(task_id: str) -> Path:
    """Каталог задачи: B1–B20 — из PRISM, B21 и далее — из tasks/ стенда."""
    matches = sorted((prism() / "tasks" / "category_b").glob(f"{task_id}_*")) + sorted(TASKS_DIR.glob(f"{task_id}_*"))
    if len(matches) != 1:
        raise SystemExit(f"задача {task_id}: найдено каталогов {len(matches)} (PRISM: {prism()}, стенд: {TASKS_DIR})")
    return matches[0]


def all_tasks() -> list[str]:
    dirs = list((prism() / "tasks" / "category_b").glob("B*_*")) + list(TASKS_DIR.glob("B*_*"))
    return sorted({d.name.split("_")[0] for d in dirs if d.is_dir()}, key=lambda t: int(t[1:]))


def parse_tasks(spec: str | list) -> list[str]:
    """B1-B20, B21-B29, «B1,B5,B7» или список."""
    items = spec if isinstance(spec, list) else re.split(r"[,\s]+", str(spec).strip())
    out: list[str] = []
    for item in filter(None, (str(i).strip() for i in items)):
        m = re.fullmatch(r"B(\d+)-B?(\d+)", item, re.I)
        out += [f"B{n}" for n in range(int(m.group(1)), int(m.group(2)) + 1)] if m else [item.upper()]
    return out


def model_slug(model: str, variant: str) -> str:
    """Имя каталога серии: <модель>__<вариант>; для 16 инструментов вариант не пишется."""
    return re.sub(r"[^\w.\-]+", "_", model) + (f"__{variant}" if variant and variant != "edt16" else "")
