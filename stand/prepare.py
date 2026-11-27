"""Подготовка стенда: каркас конфигурации, исходники проектов задач и рабочая область EDT с проектами.

Шаги (каждый пропускается, если уже сделан):
  1. выгрузка пустой конфигурации через ibcmd (лицензия не нужна) → <work>/empty-cfg;
  2. каркас с языком «Русский» и русским вариантом встроенного языка → <work>/empty-cfg-ru:
     на него рассчитаны synthconfig и сборщик прогонной конфигурации PRISM;
  3. дерево XML на задачу: объекты из config_spec.yaml + пустой общий модуль «КодКандидата» → <work>/edt-src/<id>.
     Скрытые тесты и фикстуры в дерево не попадают;
  4. настройки плагина EDT MCP в рабочей области экземпляра (порт, автозапуск) — до первого запуска EDT;
  5. EDT с полным набором инструментов, импорт проектов prism_<id> через EDT MCP, проверка модуля-заглушки и ошибок;
  6. возврат к набору из 16 инструментов.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from . import config, edt_client, edt_mode, scorer, variants

BOM = "﻿"
LANG_RU = """<?xml version="1.0" encoding="UTF-8"?>
{header}
\t<Language uuid="{uid}">
\t\t<Properties>
\t\t\t<Name>Русский</Name>
\t\t\t<Synonym>
\t\t\t\t<v8:item>
\t\t\t\t\t<v8:lang>ru</v8:lang>
\t\t\t\t\t<v8:content>Русский</v8:content>
\t\t\t\t</v8:item>
\t\t\t</Synonym>
\t\t\t<Comment/>
\t\t\t<LanguageCode>ru</LanguageCode>
\t\t</Properties>
\t</Language>
</MetaDataObject>
"""
PLUGIN_PREFS = {
    "eclipse.preferences.version": "1",
    "mcpServerAutoStart": "true",
    "mcpDestructiveConsentLevel": "allow_all",  # запись модуля без подтверждения в окне EDT
    "mcpToolPresetId": "custom",
}


def export_empty_config(dst: Path) -> None:
    if (dst / "Configuration.xml").exists():
        return
    tmp = config.work() / "empty-ib"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    ibcmd = str(config.platform_bin() / "ibcmd.exe")
    data = "--data=" + str(tmp / "data")
    for cmd in ([ibcmd, "infobase", "create", f"--db-path={tmp / 'ib'}", "--locale=ru_RU", "--create-database", data],
                [ibcmd, "infobase", "config", "export", f"--db-path={tmp / 'ib'}", data, str(dst)]):
        rc, out = scorer.run(cmd, scorer.STEP_TIMEOUT_S)
        if rc != 0:
            raise SystemExit(f"ibcmd {' '.join(cmd[1:4])}: код {rc}\n{out[-600:]}")
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"пустая конфигурация: {dst}")


def make_ru_skeleton(src: Path, dst: Path) -> None:
    """Каркас пустой конфигурации с языком «Русский» вместо English."""
    if (dst / "Configuration.xml").exists():
        return
    if (src / "Languages" / "Русский.xml").exists():  # платформа уже создала русскую конфигурацию
        shutil.copytree(src, dst)
        return
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "Languages").mkdir(exist_ok=True)
    lang = (src / "Languages" / "English.xml").read_text(encoding="utf-8-sig")
    header = re.search(r"<MetaDataObject[^>]*>", lang).group(0)
    uid = re.search(r'<Language uuid="([^"]+)"', lang).group(1)
    (dst / "Languages" / "Русский.xml").write_text(BOM + LANG_RU.format(header=header, uid=uid), encoding="utf-8")
    conf = (src / "Configuration.xml").read_text(encoding="utf-8-sig")
    for old, new in [("<DefaultLanguage>Language.English</DefaultLanguage>", "<DefaultLanguage>Language.Русский</DefaultLanguage>"),
                     ("\t\t\t<Language>English</Language>\n", "\t\t\t<Language>Русский</Language>\n"),
                     ("<ScriptVariant>English</ScriptVariant>", "<ScriptVariant>Russian</ScriptVariant>")]:
        if conf.count(old) != 1:
            raise SystemExit(f"Configuration.xml: ожидалось одно вхождение {old!r}, найдено {conf.count(old)}")
        conf = conf.replace(old, new)
    (dst / "Configuration.xml").write_text(BOM + conf, encoding="utf-8")
    print(f"каркас: {dst}")


def build_task_tree(task_id: str, skeleton: Path, out: Path) -> None:
    """Дерево XML одной задачи: объекты спеки + пустой «КодКандидата»."""
    import yaml
    from harness.execute.onec.assemble import _add_common_module
    from harness.synthconfig import build

    task_dir = config.find_task(task_id)
    spec = yaml.safe_load((task_dir / "config_spec.yaml").read_text(encoding="utf-8"))
    build(spec, skeleton, out)
    _add_common_module(out, "КодКандидата", config.STUB, servercall=False)
    conf_path = out / "Configuration.xml"
    conf = conf_path.read_text(encoding="utf-8-sig")
    anchor = "\t\t\t<Language>Русский</Language>\n"
    if conf.count(anchor) != 1 or conf.count("<Name>Configuration</Name>") != 1:
        raise SystemExit(f"{task_id}: неожиданный Configuration.xml каркаса")
    conf = conf.replace(anchor, anchor + "\t\t\t<CommonModule>КодКандидата</CommonModule>\n")
    conf = conf.replace("<Name>Configuration</Name>", f"<Name>Prism{task_id}</Name>")
    conf_path.write_text(BOM + conf, encoding="utf-8")


def ensure_plugin_prefs() -> None:
    prefs = config.workspace() / edt_mode.PREFS
    values = dict(PLUGIN_PREFS)
    if prefs.exists():
        for line in prefs.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                values.setdefault(k, v)
    values["mcpServerPort"] = str(config.instance()["port"])
    values["mcpDisabledTools"] = ",".join(sorted(set(variants.ALL_TOOLS) - variants.SERVER_MODES["full"]))
    values.pop("mcpProgressiveDisclosure", None)
    if prefs.exists() and edt_mode.edt_running():
        return  # настройки открытой EDT не правятся; набор переключит edt_mode
    prefs.parent.mkdir(parents=True, exist_ok=True)
    prefs.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n", encoding="utf-8", newline="\n")


def import_projects(tasks: list[str], src_root: Path) -> int:
    url = config.mcp_url()
    failed = 0
    for task in tasks:
        project = f"prism_{task}"
        if not (config.workspace() / project).exists():
            res = edt_client.call(url, "import_configuration_from_xml", {"importPath": str(src_root / task), "projectName": project})
            print(f"{project}: импорт — {'ошибка' if edt_client.is_error(res) else 'ок'}")
        for _ in range(120):  # проект строится после импорта
            state = edt_client.text(url, "get_problem_summary", projectName=project).lower()
            if "building" not in state and "not_available" not in state:
                break
            time.sleep(5)
    for task in tasks:  # проверка после того, как все проекты построены
        project = f"prism_{task}"
        from .agent import read_module, reset_module

        ok = False
        try:
            reset_module(project)
            ok = read_module(project).strip() == config.STUB.strip()
        except Exception as exc:  # noqa: BLE001
            print(f"{project}: сбой проверки модуля: {exc!r}")
        clean = False
        for _ in range(60):
            errors = edt_client.text(url, "get_project_errors", projectName=project, severity="ERRORS")
            if "is building" not in errors.lower():
                clean = "No Errors Found" in errors
                break
            time.sleep(5)
        if not (ok and clean):
            failed += 1
            print(f"{project}: модуль-заглушка {'да' if ok else 'НЕТ'}, ошибки: {'нет' if clean else errors[:200]}")
    return failed


def prepare(tasks: list[str] | None = None, skip_edt: bool = False) -> int:
    config.use_prism_imports()
    work = config.work()
    work.mkdir(parents=True, exist_ok=True)
    export_empty_config(work / "empty-cfg")
    make_ru_skeleton(work / "empty-cfg", work / "empty-cfg-ru")

    tasks = tasks or config.all_tasks()
    src_root = work / "edt-src"
    for task in tasks:
        out = src_root / task
        if not (out / "Configuration.xml").exists():  # готовые деревья не пересобираются: проекты EDT уже созданы из них
            shutil.rmtree(out, ignore_errors=True)
            build_task_tree(task, work / "empty-cfg-ru", out)
    print(f"исходники проектов: {len(tasks)} задач в {src_root}")
    if skip_edt:
        return 0

    ensure_plugin_prefs()
    if edt_mode.current_mode() != "full":
        edt_mode.switch("full")  # импорт идёт инструментом import_configuration_from_xml, он есть только в полном наборе
    failed = import_projects(tasks, src_root)
    print(f"проекты EDT {config.instance_no()}: {len(tasks)}, с проблемами {failed}")
    if failed:
        print("повторите bench.py prepare: готовые проекты пропускаются, проверка пройдёт заново")
        return 1
    edt_mode.switch("16")
    print(json.dumps({"workspace": str(config.workspace()), "mcp": config.mcp_url(), "tasks": len(tasks)}, ensure_ascii=False))
    return 0
