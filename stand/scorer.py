"""Оценка решения задачи PRISM категории B на локальной платформе 1С (Windows, без Docker).

Повторяет петлю harness/execute/onec/runner.py из PRISM, заменяя контейнер с учебной 1С:
  1. сборка прогонной конфигурации (assemble_run_config из PRISM: объекты задачи + КодКандидата
     + Тесты + триггер запуска) — чистый Python;
  2. база и загрузка конфигурации — ibcmd (лицензия не нужна);
  3. /CheckModules — конфигуратор (1 лицензия, процесс завершается сам);
  4. прогон скрытых тестов — тонкий клиент с /C ПрогонТеста (1 лицензия, завершается сам);
  5. вердикт — _verdict из PRISM (тот же разбор check.log и result.txt).
Шаги 3 и 4 идут под общей блокировкой: одновременно клиента 1С запускают не больше platform.license_slots оценщиков.

Платформа запускается с русским интерфейсом (/Lru /VLru): разбор журнала компиляции и маркеры
платформенных ошибок PRISM рассчитаны на русские сообщения.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import time
from pathlib import Path

from . import config

STEP_TIMEOUT_S = 180
TEST_TIMEOUT_S = 90


def run(cmd: list[str], timeout: int) -> tuple[int | None, str]:
    """Запустить процесс с пустым stdin (ibcmd без stdin бесконечно спрашивает пользователя)."""
    try:
        res = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
        return res.returncode, (res.stdout + res.stderr).decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return None, "timeout"


@contextlib.contextmanager
def license_lock(timeout_s: int = 1800):
    """Межпроцессная блокировка: каждое место лицензии — свой файл-замок в рабочем каталоге."""
    import msvcrt

    locks = config.work() / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    names = [f"license{n}.lock" for n in range(1, config.license_slots() + 1)]
    deadline = time.time() + timeout_s
    fh = None
    while fh is None:
        for name in names:
            candidate = open(locks / name, "a+b")
            try:
                candidate.seek(0)
                msvcrt.locking(candidate.fileno(), msvcrt.LK_NBLCK, 1)
                fh = candidate
                break
            except OSError:
                candidate.close()
        if fh is None:
            if time.time() > deadline:
                raise TimeoutError("место лицензии не получено за отведённое время")
            time.sleep(2)
    try:
        yield
    finally:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        fh.close()


def clients_running() -> int:
    """Сколько клиентских процессов 1С живёт на машине (каждый держит место лицензии)."""
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="replace").stdout.lower()
    return sum(out.count(f'"{name}"') for name in ("1cv8.exe", "1cv8c.exe", "1cv8s.exe"))


def create_infobase(work: Path, cfg: Path) -> str | None:
    """Файловая база из дерева XML через ibcmd. Возвращает текст ошибки или None."""
    ibcmd = str(config.platform_bin() / "ibcmd.exe")
    ib = work / "ib"
    # свой каталог автономного сервера на прогон: два ibcmd на общем каталоге падают «Server data directory lock error»
    data = ["--data=" + str(work / "ibcmd-data")]
    for cmd in ([ibcmd, "infobase", "create", f"--db-path={ib}", "--locale=ru_RU", "--create-database", *data],
                [ibcmd, "infobase", "config", "import", f"--db-path={ib}", *data, str(cfg)],
                [ibcmd, "infobase", "config", "apply", "--force", f"--db-path={ib}", *data]):
        rc, out = run(cmd, STEP_TIMEOUT_S)
        if rc != 0:
            return f"{cmd[1:4]}: код {rc}: {out[-400:]}"
    return None


def client_args(work: Path) -> list[str]:
    return [f"/F{work / 'ib'}", "/Lru", "/VLru", "/DisableStartupDialogs", "/DisableStartupMessages"]


def score(task_id: str, code: str, tag: str, no_run: bool = False) -> dict:
    config.use_prism_imports()
    import yaml
    from harness.execute.onec import runner
    from harness.execute.onec.assemble import assemble_run_config

    task_dir = config.find_task(task_id)
    task = yaml.safe_load((task_dir / "task.yaml").read_text(encoding="utf-8"))
    entry = runner.detect_entry_point(code, task.get("entry_point_patterns") or [])

    work = config.work() / "runs" / f"{task_id}-{tag}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    result_file = work / "result.txt"
    assemble_run_config(task_dir, code, entry or runner.ENTRY_PLACEHOLDER, config.work() / "empty-cfg-ru", work / "run-cfg",
                        result_path=str(result_file))

    t0 = time.time()
    error = create_infobase(work, work / "run-cfg")
    if error:
        return {"task": task_id, "status": "infra_error", "entry": entry, "detail": error}
    load_s = round(time.time() - t0, 1)
    if no_run:
        return {"task": task_id, "status": "loaded", "entry": entry, "load_s": load_s}

    platform = config.platform_bin()
    with license_lock():
        busy = clients_running()
        if busy >= config.license_limit():
            return {"task": task_id, "status": "license_busy", "entry": entry,
                    "detail": f"на машине уже {busy} клиентских процессов 1С при пределе {config.license_limit()}"}
        common = client_args(work)
        t1 = time.time()
        rc, _ = run([str(platform / "1cv8.exe"), "DESIGNER", *common, "/CheckModules", "-Server", "/Out", str(work / "check.log")],
                    STEP_TIMEOUT_S)
        (work / "check.rc").write_text("" if rc is None else str(rc), encoding="utf-8")
        check_s = round(time.time() - t1, 1)

        timed_out, test_s = False, None
        if entry is not None and "КодКандидата" not in runner._read(work / "check.log"):
            t2 = time.time()
            rc, _ = run([str(platform / "1cv8c.exe"), "ENTERPRISE", *common, "/C", "ПрогонТеста"], TEST_TIMEOUT_S)
            timed_out = rc is None
            (work / "enterprise.rc").write_text("" if rc is None else str(rc), encoding="utf-8")
            test_s = round(time.time() - t2, 1)

    verdict = runner._verdict(work, entry, timed_out)
    # каталог автономного сервера (~640 МБ) и база после вердикта не нужны: остаются журналы и run-cfg
    for name in ("ibcmd-data", "ib"):
        shutil.rmtree(work / name, ignore_errors=True)
    return {
        "task": task_id,
        "status": verdict.status,
        "passed": verdict.passed,
        "total": verdict.total,
        "solved": verdict.status == "ok" and verdict.total > 0 and verdict.passed == verdict.total,
        "entry": entry,
        "compile_errors": verdict.compile_errors[:5],
        "platform_errors": verdict.platform_errors,
        "log": verdict.log[:600],
        "infra_detail": verdict.infra_detail,
        "seconds": {"load": load_s, "check": check_s, "tests": test_s},
    }
