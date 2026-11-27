"""Обёртка: MCP-сервер стенда (stdio) с составными инструментами поверх EDT MCP.

Агент не видит инструментов EDT MCP напрямую: каждая связка «прочитал → записал → спросил ошибки» свёрнута
в один вызов, а ошибка возвращается вместе со строкой кода, к которой относится.

  inspect_project  — объекты проекта с реквизитами и текущий текст модуля КодКандидата
  write_and_check  — записать модуль целиком и сразу получить ошибки EDT со строками кода
  check_query      — проверить текст запроса
  platform_help    — справка платформы по типу и его методу
  get_code_samples — образцы типовых приёмов (stand/samples.bsl)
  run_code         — выполнить фрагмент кода на временной базе проекта (объекты задачи, данных нет)

Запускает его агент (Claude Code, --mcp-config):
  python -I stand/mcp_server.py --project prism_B1 --tools inspect_project,write_and_check,... [--strict]
Экземпляр EDT берётся из переменной PRISM_EDT_INSTANCE, настройки — из settings.yaml.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stand import config, edt_client  # noqa: E402

HERE = Path(__file__).resolve().parent
MODULE_PATH = config.MODULE_PATH

TOOLS = {
    "inspect_project": {
        "name": "inspect_project",
        "description": "Показать проект: все объекты метаданных с реквизитами, измерениями, ресурсами и типами, "
                       "и текущий текст модуля КодКандидата. Вызывай первым, до написания кода.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "write_and_check": {
        "name": "write_and_check",
        "description": "Записать модуль КодКандидата ЦЕЛИКОМ (прежний текст заменяется) и сразу проверить его. "
                       "Возвращает либо «ошибок нет», либо список ошибок с номерами строк и самими строками кода.",
        "inputSchema": {"type": "object", "properties": {"source": {"type": "string", "description": "полный текст модуля"}},
                        "required": ["source"]},
    },
    "check_query": {
        "name": "check_query",
        "description": "Проверить текст запроса 1С на объектах проекта: синтаксис, имена таблиц и полей.",
        "inputSchema": {"type": "object", "properties": {"queryText": {"type": "string"}}, "required": ["queryText"]},
    },
    "platform_help": {
        "name": "platform_help",
        "description": "Справка платформы 1С: методы и свойства типа (например ТаблицаЗначений, Запрос, Структура). "
                       "С memberName — описание одного метода с параметрами. Проверяй так методы, в которых не уверен.",
        "inputSchema": {"type": "object", "properties": {"typeName": {"type": "string"}, "memberName": {"type": "string"}},
                        "required": ["typeName"]},
    },
    "get_code_samples": {
        "name": "get_code_samples",
        "description": "Образцы типовых приёмов встроенного языка 1С и языка запросов: таблица значений, запрос и выборка, "
                       "виртуальные таблицы регистров, иерархия, константы, план счетов.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "run_code": {
        "name": "run_code",
        "description": "Выполнить фрагмент кода 1С на сервере тестовой базы проекта и вернуть результат или текст ошибки. "
                       "В базе объекты метаданных проекта и текущий модуль КодКандидата; данных нет (таблицы пустые). "
                       "Во фрагменте вызывай свою функцию: Результат = КодКандидата.МояФункция(...). "
                       "Значение переменной Результат вернётся строкой. Ссылки можно брать пустые: "
                       "Справочники.Склады.ПустаяСсылка().",
        "inputSchema": {"type": "object", "properties": {"code": {"type": "string", "description": "фрагмент кода 1С"}},
                        "required": ["code"]},
    },
}


def edt(tool: str, **args) -> str:
    return edt_client.text(config.mcp_url(), tool, **args)


def inspect_project(project: str) -> str:
    listing = edt("get_metadata_objects", projectName=project, limit=200)
    fqns = [f"{m.group(2)}.{m.group(1)}" for m in re.finditer(r"^\| (\S+) \|[^|]*\|[^|]*\| (\w+) \|", listing, re.M)
            if m.group(2) not in ("Type", "CommonModule", "Language")]
    details = edt("get_metadata_details", projectName=project, objectFqns=fqns, language="ru") if fqns else "(объектов нет)"
    module = edt("read_module_source", projectName=project, modulePath=MODULE_PATH)
    return f"{listing}\n\n# Состав объектов\n{details}\n\n# Модуль КодКандидата сейчас\n{module}"


# Аннотации, допустимые перед методом общего модуля; остальное платформа не компилирует, а EDT ошибкой не считает
ANNOTATIONS = {"наклиенте", "насервере", "насерверебезконтекста", "наклиентенасервере", "наклиентенасерверебезконтекста",
               "перед", "после", "вместо", "изменениеиконтроль",
               "atclient", "atserver", "atservernocontext", "atclientatserver", "atclientatservernocontext",
               "before", "after", "around", "changeandvalidate"}
EXPORT_SAMPLE = """// Объявление экспортной функции общего модуля: слово Экспорт стоит ПОСЛЕ скобок, без аннотаций и без «&».
Функция ПримерЭкспортнойФункции(Параметр1, Параметр2 = Неопределено) Экспорт
	Возврат Параметр1;
КонецФункции

"""


def declaration_issues(source: str) -> list[str]:
    """Ошибки объявления, которые EDT не показывает, а скрытые тесты ловят: выдуманные аннотации и отсутствие экспорта."""
    out = []
    for n, line in enumerate(source.splitlines(), 1):
        m = re.match(r"\s*&\s*(\w+)", line)
        if m and m.group(1).lower() not in ANNOTATIONS:
            out.append(f"- строка {n}: аннотации &{m.group(1)} не существует, удали эту строку. Экспорт объявляется словом "
                       f"Экспорт после скобок: Функция Имя(Параметры) Экспорт\n      {line.strip()}")
    code = re.sub(r"//[^\n]*", "", source)
    if not re.search(r"^\s*(?:Функция|Процедура|Function|Procedure)\s+\w+\s*\([^)]*\)\s*(?:Экспорт|Export)\b", code, re.M | re.I):
        out.append("- в модуле нет экспортной функции: тесты вызывают её снаружи. Объявление должно быть "
                   "Функция Имя(Параметры) Экспорт — слово Экспорт после закрывающей скобки.")
    return out


def write_and_check(project: str, source: str, strict: bool = False) -> str:
    written = edt("write_module_source", projectName=project, modulePath=MODULE_PATH, source=source, mode="replace", overwrite=True)
    # EDT не пишет модуль с незакрытыми блоками («Write blocked»): прежний текст остаётся, и ошибок по нему нет
    if "status: success" not in written:
        try:
            reason = json.loads(written).get("error") or written
        except ValueError:
            reason = written
        return ("Модуль НЕ записан: в тексте синтаксические ошибки, прежний текст модуля остался без изменений. "
                "Исправь и запиши снова.\n" + str(reason).replace("\n\nPass skipSyntaxCheck=true to force write.", "")[:1500])
    time.sleep(3)  # EDT перепроверяет модуль после записи
    errors = edt("get_project_errors", projectName=project, severity="ERRORS", objects=["КодКандидата"], responseFormat="detailed")
    extra = declaration_issues(source) if strict else []
    head = "Модуль записан, но в нём есть ошибки. Исправь именно эти места и запиши модуль снова.\n"
    if "No Errors Found" in errors:
        return head + "\n".join(extra) if extra else "Модуль записан. Ошибок нет."
    lines = source.splitlines()
    # строки таблицы EDT: | описание | объект | путь | номер строки | код проверки | …
    rows = re.findall(r"^\| (.+?) \| [^|]* \| [^|]* \| (\d+) \|", errors, re.M)
    if not rows:
        return head + "\n".join(extra) + "\n" + errors[:3000]
    out = []
    for message, number in sorted(rows, key=lambda r: int(r[1])):
        n = int(number)
        out.append(f"- строка {n}: {message}" + (f"\n      {lines[n - 1].strip()}" if 0 < n <= len(lines) else ""))
    return head + "\n".join(extra + out)


def check_query(project: str, query: str) -> str:
    raw = edt("validate_query", projectName=project, queryText=query)
    try:
        issues = [i for i in json.loads(raw).get("issues", []) if i.get("severity") == "ERROR"]
    except ValueError:
        return raw[:2000]
    if not issues:
        return "Запрос верен."
    lines, seen, out = query.splitlines(), set(), ["В запросе ошибки:"]
    for i in issues:
        key = (i.get("line"), i.get("message"))
        if key in seen:
            continue
        seen.add(key)
        n = i.get("line") or 0
        out.append(f"- строка {n}: {i.get('message')}" + (f"\n      {lines[n - 1].strip()}" if 0 < n <= len(lines) else ""))
    return "\n".join(out)


RUNNER = """Функция ПрогнатьТест() Экспорт
	УстановитьБезопасныйРежим(Истина);
	Результат = Неопределено;
	Попытка
{code}
	Исключение
		Возврат "ОШИБКА: " + ПодробноеПредставлениеОшибки(ИнформацияОбОшибке());
	КонецПопытки;
	Возврат "Выполнено без ошибок. Результат = " + ПредставлениеРезультата(Результат);
КонецФункции

Функция ПредставлениеРезультата(Значение)
	Если ТипЗнч(Значение) = Тип("ТаблицаЗначений") Тогда
		Имена = "";
		Для Каждого Колонка Из Значение.Колонки Цикл
			Имена = Имена + ?(Имена = "", "", ", ") + Колонка.Имя;
		КонецЦикла;
		Возврат "ТаблицаЗначений, строк: " + Значение.Количество() + ", колонки: " + Имена;
	ИначеЕсли ТипЗнч(Значение) = Тип("Массив") Тогда
		Возврат "Массив, элементов: " + Значение.Количество();
	КонецЕсли;
	Возврат "" + Значение + " (" + ТипЗнч(Значение) + ")";
КонецФункции
"""


def run_code(project: str, code: str) -> str:
    """Фрагмент выполняется в безопасном режиме платформы на временной базе: объекты задачи и текущий модуль кандидата."""
    config.use_prism_imports()
    import yaml
    from harness.execute.onec.assemble import BOM, _HANDLER, _add_common_module
    from harness.synthconfig import build

    from stand import scorer

    task_id = project.replace("prism_", "")
    spec = yaml.safe_load((config.find_task(task_id) / "config_spec.yaml").read_text(encoding="utf-8"))
    candidate = (config.workspace() / project / "src" / "CommonModules" / "КодКандидата" / "Module.bsl").read_text(encoding="utf-8-sig")

    work = config.work() / "runs" / f"exec-{project}-{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True)
    try:
        cfg = work / "run-cfg"
        build(spec, config.work() / "empty-cfg-ru", cfg)
        _add_common_module(cfg, "КодКандидата", candidate, servercall=False)
        body = "\n".join("\t\t" + line for line in code.splitlines())
        _add_common_module(cfg, "Тесты", RUNNER.format(code=body), servercall=True)
        result_file = work / "result.txt"
        (cfg / "Ext").mkdir(exist_ok=True)
        (cfg / "Ext" / "ManagedApplicationModule.bsl").write_text(BOM + _HANDLER.format(result_path=str(result_file)), encoding="utf-8")
        conf = cfg / "Configuration.xml"
        text = conf.read_text(encoding="utf-8-sig")
        anchor = "\t\t\t<Language>Русский</Language>\n"
        conf.write_text(BOM + text.replace(anchor, anchor + "\t\t\t<CommonModule>КодКандидата</CommonModule>\n"
                                                    "\t\t\t<CommonModule>Тесты</CommonModule>\n", 1), encoding="utf-8")

        error = scorer.create_infobase(work, cfg)
        if error:
            return "Не удалось подготовить тестовую базу (сбой стенда, не твоя ошибка): " + error[-300:]
        common, platform = scorer.client_args(work), config.platform_bin()
        with scorer.license_lock():
            if scorer.clients_running() >= config.license_limit():
                return "Тестовая база сейчас занята (нет свободной лицензии). Повтори вызов позже."
            # сначала компиляция: ошибки модуля иначе выглядят как молчаливый отказ клиента
            scorer.run([str(platform / "1cv8.exe"), "DESIGNER", *common, "/CheckModules", "-Server", "/Out", str(work / "check.log")],
                       scorer.STEP_TIMEOUT_S)
            check = (work / "check.log").read_text(encoding="utf-8-sig", errors="replace") if (work / "check.log").exists() else ""
            if "{" in check and ("КодКандидата" in check or "Тесты" in check):
                return "ОШИБКА КОМПИЛЯЦИИ:\n" + check.replace("ОбщийМодуль.Тесты.Модуль", "Фрагмент")[:1500]
            rc, _ = scorer.run([str(platform / "1cv8c.exe"), "ENTERPRISE", *common, "/C", "ПрогонТеста"], scorer.TEST_TIMEOUT_S)
        if result_file.exists():
            return result_file.read_text(encoding="utf-8-sig", errors="replace")[:2500].replace("ОбщийМодуль.Тесты.Модуль", "Фрагмент")
        return "Выполнение не завершилось за отведённое время (возможен бесконечный цикл)." if rc is None else "Результат не получен."
    finally:
        shutil.rmtree(work, ignore_errors=True)


def handle(name: str, a: dict, project: str, strict: bool) -> str:
    if name == "inspect_project":
        return inspect_project(project)
    if name == "write_and_check":
        return write_and_check(project, str(a.get("source", "")), strict)
    if name == "check_query":
        return check_query(project, str(a.get("queryText", "")))
    if name == "platform_help":
        extra = {"memberName": a["memberName"]} if a.get("memberName") else {}
        return edt("get_platform_documentation", projectName=project, typeName=str(a.get("typeName", "")),
                   language="ru", responseFormat="detailed", **extra)[:6000]
    if name == "run_code":
        t0 = time.time()
        return run_code(project, str(a.get("code", ""))) + f"\n[{time.time() - t0:.0f} с]"
    return (EXPORT_SAMPLE if strict else "") + (HERE / "samples.bsl").read_text(encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--tools", required=True)
    ap.add_argument("--strict", action="store_true", help="проверять объявление экспорта и аннотации, дать образец экспортной функции")
    args = ap.parse_args()
    enabled = [t for t in args.tools.split(",") if t in TOOLS]
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

    def send(obj):
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid, method = msg.get("id"), msg.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2025-03-26"),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "prism-edt-agent", "version": "1"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [TOOLS[t] for t in enabled]}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            try:
                text = handle(name, params.get("arguments") or {}, args.project, args.strict) if name in enabled \
                    else f"неизвестный инструмент: {name}"
                send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}]}})
            except Exception as exc:  # сбой стенда не должен ронять сессию агента
                send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": f"Сбой стенда: {exc!r}"}], "isError": True}})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}})


if __name__ == "__main__":
    main()
