"""Варианты теста: какие инструменты видит агент, какой набор должен отдавать сервер EDT MCP и какое условие получает модель.

Новый вариант — новая запись в VARIANTS. Поля:
  title       — название в отчёте;
  edt_mode    — набор инструментов, который должен быть включён на сервере EDT (bench.py mode <набор>); None — EDT не нужна;
  edt_tools   — какие инструменты EDT агенту разрешены (остальные видимые запрещены); [] — сервер EDT агенту не подключается;
  bench_tools — инструменты MCP-сервера стенда (servers/): составные (compose) или добавочные (run_code);
  prompt      — шаблон условия: edt, compose или notools;
  strict      — строгая обёртка: образец экспортной функции и проверка объявления в write_and_check.
"""

from __future__ import annotations

# 16 инструментов EDT MCP, которые разрешены агенту в базовом варианте: чтение метаданных и кода, запись модуля, проверки, справка
EDT16 = [
    "get_metadata_objects", "get_metadata_details", "list_modules", "get_module_structure",
    "read_module_source", "read_method_source", "write_module_source",
    "get_project_errors", "get_problem_summary", "get_check_description", "validate_query",
    "get_platform_documentation", "get_content_assist", "get_symbol_info",
    "search_in_code", "get_tool_guide",
]
EDT6 = ["get_metadata_objects", "get_metadata_details", "read_module_source", "write_module_source",
        "get_project_errors", "validate_query"]
# Безвредные инструменты сверх 16: модель их видит и может вызвать в вариантах full и groups.
# Запуск 1С, базы, git, проекты и правка метаданных видны, но вызов получает отказ: они заняли бы лицензию
# или испортили проект задачи для следующих прогонов.
FULL_EXTRA = [
    "apply_quick_fix", "business_process_route_map", "cancel_job", "compare_configurations", "dcs", "debug_pause", "debug_status",
    "evaluate_expression", "export_common_picture", "find_references", "get_applications", "get_comparison_node",
    "get_configuration_properties", "get_edt_version", "get_event_log", "get_form_layout_snapshot", "get_form_screenshot",
    "get_job_status", "get_markers", "get_mcp_history", "get_method_call_hierarchy", "get_objects_by_tags",
    "get_outgoing_structures", "get_profiling_results", "get_server_status", "get_subsystem_content", "get_tags",
    "get_template_screenshot", "get_translation_project_info", "get_variables", "go_to_definition", "list_breakpoints",
    "list_common_pictures", "list_configurations", "list_git_branches", "list_projects", "list_subsystems", "list_toolsets",
    "remove_breakpoint", "resume", "resync_to_disk", "revalidate_objects", "search_in_dcs", "set_breakpoint",
    "set_error_breakpoint", "set_variable", "step", "validate_form_model", "validate_xdto_package", "wait_for_break",
]
# Набор из 30 для регулярной работы: на сервере включены 30, агенту разрешены 24; последние шесть видны, но запрещены.
SET30 = EDT16 + ["list_projects", "find_references", "go_to_definition", "get_method_call_hierarchy", "revalidate_objects",
                 "resync_to_disk", "get_job_status", "validate_form_model",
                 "update_database", "infobase_sessions", "create_metadata", "modify_metadata", "adopt_metadata_object", "run_yaxunit_tests"]
# Остальные инструменты сервера (плагин EDT MCP 2.22.1): вместе с перечисленными выше дают все 102.
OTHER = [
    "adopt_metadata_object", "ask_workmate", "build_external_objects", "clean_project", "create_git_branch", "create_infobase",
    "create_launch_config", "create_metadata", "create_project", "debug_yaxunit_tests", "delete_infobase", "delete_launch_config",
    "delete_metadata", "delete_project", "enable_toolset", "export_configuration_to_file", "export_configuration_to_xml",
    "generate_translation_strings", "git", "import_configuration_from_xml", "import_project_from_file", "infobase_sessions", "launch",
    "merge_rules", "modify_metadata", "project_checks", "rename_metadata_object", "run_yaxunit_tests", "set_branch_infobase",
    "set_infobase_credentials", "start_profiling", "stop_profiling", "switch_git_branch", "terminate_launch", "translate_configuration",
    "update_database",
]
ALL_TOOLS = sorted(set(EDT16) | set(FULL_EXTRA) | set(OTHER))
PLUGIN_OFF = ["git", "ask_workmate", "get_form_screenshot"]  # выключены в плагине по умолчанию
GROUPS = ["metadata", "code", "debug", "testing", "profiling", "forms", "tags", "translation", "project", "comparison", "git"]

# Наборы инструментов сервера EDT MCP (bench.py mode): что включено на сервере
SERVER_MODES = {
    "16": set(EDT16),
    "full": set(ALL_TOOLS),
    "set30": set(SET30),
    "groups": set(ALL_TOOLS) - set(PLUGIN_OFF),  # плюс mcpProgressiveDisclosure: видна только группа core
}
SERVER_MODE_EXPECTED = {"16": 16, "full": 102, "set30": 30, "groups": 12}  # сколько инструментов отдаёт сервер в наборе

COMPOSE = ["inspect_project", "write_and_check", "check_query", "platform_help", "get_code_samples"]

VARIANTS = {
    "notools": {"title": "без инструментов", "edt_mode": None, "edt_tools": [], "bench_tools": [], "prompt": "notools"},
    "edt16": {"title": "16 инструментов", "edt_mode": "16", "edt_tools": EDT16, "bench_tools": [], "prompt": "edt"},
    "tools6": {"title": "6 инструментов", "edt_mode": "16", "edt_tools": EDT6, "bench_tools": [], "prompt": "edt"},
    "set30": {"title": "набор из 30", "edt_mode": "set30", "edt_tools": SET30[:-6], "bench_tools": [], "prompt": "edt"},
    "groups": {"title": "встроенные группы", "edt_mode": "groups", "edt_tools": EDT16 + FULL_EXTRA + ["enable_toolset"],
               "bench_tools": [], "prompt": "edt", "groups": True},
    "full": {"title": "полный набор (102)", "edt_mode": "full", "edt_tools": EDT16 + FULL_EXTRA, "bench_tools": [], "prompt": "edt"},
    "compose": {"title": "составные", "edt_mode": "16", "edt_tools": [], "bench_tools": COMPOSE, "prompt": "compose"},
    "composerun": {"title": "составные + запуск кода", "edt_mode": "16", "edt_tools": [], "bench_tools": COMPOSE + ["run_code"],
                   "prompt": "compose"},
    "composerun2": {"title": "строгая обёртка + запуск", "edt_mode": "16", "edt_tools": [], "bench_tools": COMPOSE + ["run_code"],
                    "prompt": "compose", "strict": True},
}

PROMPT_EDT = """Ты работаешь в проекте 1С:EDT `{project}` через инструменты EDT MCP (сервер `edt`).

Задача:
{task}

Правила работы:
- Решение — экспортная функция или процедура в общем серверном модуле `КодКандидата` проекта `{project}`
  (путь модуля: `{module}`). Запиши её туда инструментом `write_module_source`.
- Имена объектов метаданных, реквизитов, измерений и ресурсов бери только из проекта: посмотри их
  инструментами `get_metadata_objects` и `get_metadata_details`. Метаданные не изменяй.
- После записи проверь модуль инструментом `get_project_errors` и исправь ошибки в своём коде.
  Обязательны к исправлению только ошибки (уровень ERRORS): синтаксис, неизвестные имена, неверные запросы.
  Замечания стандартов оформления (комментарии к методам, области, именование) исправлять не нужно.
  Замечания к метаданным (представления объектов, название модуля `КодКандидата`) были до тебя — их не трогай.
- Работай только с проектом `{project}`: другие проекты не читай и не меняй.
{extra}- Когда функция записана и ошибок в модуле нет, ответь одним словом: ГОТОВО.
"""
GROUPS_RULE = ("- Часть инструментов сервера скрыта. Если нужного инструмента нет в списке, найди его группу через `list_toolsets`\n"
               "  и открой её через `enable_toolset`.\n")

PROMPT_COMPOSE = """Ты работаешь в проекте 1С:EDT `{project}` через {count} инструментов сервера `bench`.

Задача:
{task}

Правила работы:
- Решение — экспортная функция или процедура в общем серверном модуле `КодКандидата` (путь модуля: `{module}`).
- Сначала вызови `inspect_project`: имена объектов метаданных, реквизитов, измерений и ресурсов бери только оттуда.
- Образцы типовых приёмов 1С смотри в `get_code_samples`, методы платформы, в которых не уверен, — в `platform_help`,
  текст запроса проверяй в `check_query`.
- Модуль записывай инструментом `write_and_check`: он сразу возвращает ошибки со строками кода. Исправь эти места
  и запиши снова. Замечания стандартов оформления исправлять не нужно — инструмент показывает только ошибки.
{done}
"""
DONE_COMPOSE = "- Когда функция записана и `write_and_check` ответил «Ошибок нет», ответь одним словом: ГОТОВО."
# Правило остановки обязательно: без него модель после удачного запуска продолжает перепроверять до предела времени
DONE_RUN = ("- Когда `write_and_check` ответил «Ошибок нет», выполни свою функцию инструментом `run_code`: он запускает код\n"
            "  на тестовой базе проекта (таблицы пустые) и возвращает результат или текст ошибки. Ошибку выполнения\n"
            "  («Метод объекта не обнаружен», ошибки запроса) исправь через `write_and_check` и запусти снова.\n"
            "- Как только `run_code` ответил «Выполнено без ошибок» и результат того типа, который требует задача,\n"
            "  сразу ответь одним словом: ГОТОВО. Больше ничего не перепроверяй и не переписывай.")

PROMPT_NOTOOLS = """Напиши код на встроенном языке 1С:Предприятие 8.

Задача:
{task}

Правила:
- Решение — экспортная функция или процедура в общем серверном модуле `КодКандидата`.
- Инструментов у тебя нет: проверить код нельзя, ответ один.
- Имена объектов метаданных, реквизитов, измерений и ресурсов бери только из описания проекта ниже.
- В ответе дай полный текст модуля в одном блоке ```bsl и больше ничего.

# Описание проекта
{metadata}
"""

NUMBERS = {5: "пять", 6: "шесть"}


def get(name: str) -> dict:
    if name not in VARIANTS:
        raise SystemExit(f"неизвестный вариант {name}; есть: {', '.join(VARIANTS)}")
    return VARIANTS[name]


def prompt(name: str, project: str, task_text: str, module: str, metadata: str = "") -> str:
    v = get(name)
    if v["prompt"] == "notools":
        return PROMPT_NOTOOLS.format(task=task_text, metadata=metadata)
    if v["prompt"] == "compose":
        n = len(v["bench_tools"])
        return PROMPT_COMPOSE.format(project=project, task=task_text, module=module, count=NUMBERS.get(n, str(n)),
                                     done=DONE_RUN if "run_code" in v["bench_tools"] else DONE_COMPOSE)
    return PROMPT_EDT.format(project=project, task=task_text, module=module, extra=GROUPS_RULE if v.get("groups") else "")
