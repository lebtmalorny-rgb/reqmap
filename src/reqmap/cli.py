"""Русский CLI для автономного анализа требований и проверки локальной KB."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback

from reqmap import __version__
from reqmap.config import AppConfig, load_config
from reqmap.crosscheck import CrosscheckIssue, crosscheck
from reqmap.errors import ReqmapError
from reqmap.export_json import symlink_component, write_canonical_json
from reqmap.export_markdown import write_markdown
from reqmap.export_xlsx import write_xlsx
from reqmap.ids import generated_requirement_id
from reqmap.input_text import load_text
from reqmap.input_xlsx import load_xlsx_bytes
from reqmap.knowledge import load_knowledge
from reqmap.llm import OpenAICompatibleClient
from reqmap.manifest import RunLogger, write_manifest
from reqmap.models import (
    AnalysisRequest,
    AnalysisState,
    Requirement,
    RunResult,
    SourceCoordinate,
)
from reqmap.pipeline import analyze


_FINAL_ARTIFACTS = (
    "result.json",
    "result.xlsx",
    "report.md",
    "run.jsonl",
    "manifest.json",
)
_PREFLIGHT_ARTIFACTS = ("run.jsonl", "manifest.json")
_STATUS_EXIT_CODES = {"SUCCESS": 0, "PARTIAL": 4, "FAILED": 6}


class _UsageError(Exception):
    pass


class _RussianArgumentParser(argparse.ArgumentParser):
    def format_help(self) -> str:
        return super().format_help().replace(
            "usage:",
            "использование:",
            1,
        )

    def format_usage(self) -> str:
        return super().format_usage().replace(
            "usage:",
            "использование:",
            1,
        )

    def error(self, message: str) -> None:
        raise _UsageError(_localize_argument_error(message))


def _localize_argument_error(message: str) -> str:
    required_prefix = "the following arguments are required: "
    if message.startswith(required_prefix):
        return (
            "не заданы обязательные аргументы: "
            f"{message.removeprefix(required_prefix)}"
        )

    unknown_prefix = "unrecognized arguments: "
    if message.startswith(unknown_prefix):
        return (
            "неизвестные аргументы: "
            f"{message.removeprefix(unknown_prefix)}"
        )

    if message.startswith("argument "):
        argument, separator, detail = message.partition(": ")
        argument = argument.removeprefix("argument ")
        if separator and detail.startswith("invalid choice: "):
            value = detail.removeprefix("invalid choice: ").partition(
                " (choose from "
            )[0]
            return (
                f"для {argument} указано недопустимое значение {value}; "
                "допустимые значения приведены в --help."
            )
        if separator and detail.startswith("not allowed with argument "):
            other = detail.removeprefix("not allowed with argument ")
            return f"{argument} нельзя использовать одновременно с {other}."
        if separator and detail in {
            "expected one argument",
            "expected at least one argument",
        }:
            return f"для {argument} не указано значение."

    return "некорректные аргументы; проверьте синтаксис через --help."


def main(argv: list[str] | None = None) -> int:
    """Выполнить CLI без traceback, если пользователь не запросил --debug."""
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["--version"]:
        print(f"reqmap {__version__}")
        return 0
    parser = _build_parser()
    try:
        parsed = parser.parse_args(arguments)
    except _UsageError as exc:
        print(f"Ошибка аргументов командной строки: {exc}", file=sys.stderr)
        return 2
    except SystemExit as exc:
        return int(exc.code)

    if parsed.command == "analyze":
        return _analyze_command(parsed)
    if parsed.command == "knowledge" and parsed.knowledge_command == "validate":
        return _knowledge_validate_command(parsed)
    print("Ошибка: не выбрана команда reqmap.", file=sys.stderr)
    return 2


def _build_parser() -> _RussianArgumentParser:
    parser = _RussianArgumentParser(
        prog="reqmap",
        description="Сопоставление требований с OpenStack Epoxy 2025.1",
        add_help=False,
    )
    _localize_parser(parser)
    subparsers = parser.add_subparsers(dest="command")

    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Проанализировать требования",
        description="Проанализировать требования локальным reqmap.",
        add_help=False,
    )
    _localize_parser(analyze_parser)
    analyze_parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        help="Путь к исходному XLSX",
    )
    source_group = analyze_parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--requirement",
        dest="requirements",
        action="append",
        help="Формулировка требования; параметр можно повторять",
    )
    source_group.add_argument(
        "--stdin",
        action="store_true",
        help="Прочитать требования из stdin",
    )
    analyze_parser.add_argument(
        "--text-mode",
        choices=("single", "lines"),
        default="single",
        help="Режим разбора stdin: единый текст или непустые строки",
    )
    analyze_parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Путь к конфигурации reqmap",
    )
    analyze_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Каталог выходных артефактов",
    )
    analyze_parser.add_argument(
        "--debug",
        action="store_true",
        help="Показать traceback внутренней ошибки",
    )

    knowledge_parser = subparsers.add_parser(
        "knowledge",
        help="Операции с локальной базой знаний",
        add_help=False,
    )
    _localize_parser(knowledge_parser)
    knowledge_subparsers = knowledge_parser.add_subparsers(
        dest="knowledge_command"
    )
    validate_parser = knowledge_subparsers.add_parser(
        "validate",
        help="Проверить локальный snapshot базы знаний",
        add_help=False,
    )
    _localize_parser(validate_parser)
    validate_parser.add_argument(
        "--path",
        required=True,
        type=Path,
        help="Путь к snapshot базы знаний",
    )
    validate_parser.add_argument(
        "--debug",
        action="store_true",
        help="Показать traceback внутренней ошибки",
    )
    return parser


def _localize_parser(parser: argparse.ArgumentParser) -> None:
    parser._positionals.title = "позиционные аргументы"
    parser._optionals.title = "параметры"
    parser.add_argument(
        "-h",
        "--help",
        action="help",
        help="Показать эту справку и выйти",
    )


def _analyze_command(arguments: argparse.Namespace) -> int:
    debug = bool(arguments.debug)
    try:
        config = load_config(arguments.config, os.environ)
        request = _analysis_request(arguments, config)
        model = OpenAICompatibleClient(config.model)
    except Exception as exc:
        _report_exception(
            "Не удалось подготовить анализ требований.",
            exc,
            debug,
        )
        return 2

    try:
        run = analyze(request, config, model)
    except Exception as exc:
        _report_exception(
            "Анализ требований завершился внутренней ошибкой.",
            exc,
            debug,
        )
        return 6

    if run.metadata.get("preflight_ok") is False:
        _print_run_summary(run)
        if run.metadata.get("preflight_artifacts_written") is True:
            _print_artifacts(request.output_dir, _PREFLIGHT_ARTIFACTS)
        return 3

    try:
        issues = _publish_artifacts(run, request.output_dir, config)
    except Exception as exc:
        _report_exception(
            "Не удалось создать или проверить выходные артефакты.",
            exc,
            debug,
        )
        _record_export_failure(run, request.output_dir, config, exc)
        _print_run_summary(run)
        _print_artifacts(request.output_dir, _FINAL_ARTIFACTS)
        return 5

    _print_run_summary(run)
    _print_artifacts(request.output_dir, _FINAL_ARTIFACTS)
    if issues:
        for issue in issues:
            print(f"Ошибка {issue.code}: {issue.message_ru}", file=sys.stderr)
        return 5
    return _STATUS_EXIT_CODES[run.run_status]


def _analysis_request(
    arguments: argparse.Namespace,
    config: AppConfig,
) -> AnalysisRequest:
    sources = sum(
        (
            arguments.input is not None,
            arguments.requirements is not None,
            bool(arguments.stdin),
        )
    )
    if sources != 1:
        raise _UsageError(
            "укажите ровно один источник: INPUT.xlsx, --requirement или --stdin."
        )

    source_path: Path | None = None
    if arguments.input is not None:
        source_path = arguments.input
        _reject_source_output_collision(source_path, arguments.output)
        raw_input = source_path.read_bytes()
        requirements = load_xlsx_bytes(
            raw_input,
            config.input_profile,
            source_path.name,
        )
        input_kind = "xlsx"
    elif arguments.requirements is not None:
        values = tuple(arguments.requirements)
        if any(not value.strip() for value in values):
            raise _UsageError("--requirement должен содержать непустой текст.")
        raw_input = (
            json.dumps(
                values,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        requirements = _requirements_from_arguments(values)
        input_kind = "text"
    else:
        raw_input = _read_stdin_bytes()
        try:
            text = raw_input.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _UsageError("stdin должен быть текстом UTF-8.") from exc
        requirements = load_text(text, arguments.text_mode, "<stdin>")
        input_kind = "text"

    if not requirements:
        raise _UsageError("Источник не содержит ни одного непустого требования.")
    return AnalysisRequest(
        requirements=requirements,
        input_sha256=hashlib.sha256(raw_input).hexdigest(),
        input_kind=input_kind,
        source_path=source_path,
        output_dir=arguments.output,
    )


def _reject_source_output_collision(source: Path, output: Path) -> None:
    collisions = tuple(
        name
        for name in _FINAL_ARTIFACTS
        if _paths_same(source, output / name)
    )
    if collisions:
        raise _UsageError(
            "Исходный XLSX совпадает с выходным артефактом: "
            f"{', '.join(collisions)}. Укажите другой --output."
        )


def _paths_same(first: Path, second: Path) -> bool:
    try:
        return first.resolve(strict=False) == second.resolve(strict=False)
    except OSError:
        return first.absolute() == second.absolute()


def _requirements_from_arguments(values: tuple[str, ...]) -> tuple[Requirement, ...]:
    return tuple(
        Requirement(
            requirement_id=generated_requirement_id(ordinal),
            source_id=None,
            text=value,
            ordinal=ordinal,
            coordinate=SourceCoordinate(
                source_name="--requirement",
                sheet=None,
                row=ordinal,
            ),
        )
        for ordinal, value in enumerate(values, start=1)
    )


def _read_stdin_bytes() -> bytes:
    binary = getattr(sys.stdin, "buffer", None)
    if binary is not None:
        value = binary.read()
        if not isinstance(value, bytes):
            raise _UsageError("Не удалось прочитать stdin как UTF-8 bytes.")
        return value
    text = sys.stdin.read()
    if not isinstance(text, str):
        raise _UsageError("Не удалось прочитать stdin как текст.")
    return text.encode("utf-8")


def _publish_artifacts(
    run: RunResult,
    output: Path,
    config: AppConfig,
) -> tuple[CrosscheckIssue, ...]:
    paths = {name: output / name for name in _FINAL_ARTIFACTS}
    logger = RunLogger(
        paths["run.jsonl"],
        redacted_values=_redacted_values(config),
    )
    logger.write(
        "analysis_finished",
        "info",
        "Предметный анализ требований завершён.",
        run_status=run.run_status,
        requirements_count=len(run.requirements),
    )
    artifact_hashes = {
        "result.json": write_canonical_json(run, paths["result.json"]),
        "result.xlsx": write_xlsx(run, paths["result.xlsx"]),
        "report.md": write_markdown(run, paths["report.md"]),
    }
    issues = crosscheck(
        run,
        paths["result.json"],
        paths["result.xlsx"],
        paths["report.md"],
    )
    if issues:
        logger.write(
            "crosscheck_failed",
            "error",
            "Обнаружены расхождения выходных артефактов.",
            issue_codes=[item.code for item in issues],
        )
    else:
        logger.write(
            "artifacts_verified",
            "info",
            "JSON, XLSX и Markdown согласованы с canonical RunResult.",
        )
    artifact_hashes["run.jsonl"] = _sha256_file(paths["run.jsonl"])
    write_manifest(run, artifact_hashes, paths["manifest.json"])
    return issues


def _record_export_failure(
    run: RunResult,
    output: Path,
    config: AppConfig,
    error: Exception,
) -> None:
    paths = {name: output / name for name in _FINAL_ARTIFACTS}
    try:
        RunLogger(
            paths["run.jsonl"],
            redacted_values=_redacted_values(config),
        ).write(
            "export_failed",
            "error",
            "Создание выходных артефактов завершилось ошибкой.",
            error_type=type(error).__name__,
        )
        hashes = {
            name: _sha256_file(path)
            for name, path in paths.items()
            if name != "manifest.json" and path.is_file() and not path.is_symlink()
        }
        write_manifest(run, hashes, paths["manifest.json"])
    except (OSError, ValueError, ReqmapError):
        return


def _redacted_values(config: AppConfig) -> tuple[str, ...]:
    api_key = config.model.api_key
    return () if not api_key else (api_key,)


def _knowledge_validate_command(arguments: argparse.Namespace) -> int:
    try:
        knowledge = load_knowledge(arguments.path)
    except Exception as exc:
        _report_exception(
            "Проверка базы знаний завершилась ошибкой.",
            exc,
            bool(arguments.debug),
        )
        return 2
    print(
        "База знаний OpenStack "
        f"{knowledge.release} проверена: "
        f"components={len(knowledge.components)}, "
        f"capabilities={len(knowledge.capabilities)}, "
        f"evidence={len(knowledge.evidence)}, "
        f"SHA-256={knowledge.snapshot_sha256}."
    )
    return 0


def _print_run_summary(run: RunResult) -> None:
    print(f"Статус запуска: {run.run_status}")
    incomplete = tuple(
        item.requirement.requirement_id
        for item in run.requirements
        if item.analysis_state is not AnalysisState.COMPLETED
    )
    if incomplete:
        print(f"Незавершённые требования: {', '.join(incomplete)}")


def _print_artifacts(output: Path, names: tuple[str, ...]) -> None:
    for name in names:
        path = output / name
        if (
            symlink_component(path) is None
            and path.is_file()
            and not path.is_symlink()
        ):
            print(f"{path.resolve()} SHA-256 {_sha256_file(path)}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _report_exception(message: str, error: Exception, debug: bool) -> None:
    if debug:
        traceback.print_exception(error, file=sys.stderr)
        return
    if isinstance(error, ReqmapError):
        print(f"Ошибка {error.code}: {error.message_ru}", file=sys.stderr)
    elif isinstance(error, _UsageError):
        print(f"Ошибка: {error}", file=sys.stderr)
    else:
        print(f"Ошибка: {message}", file=sys.stderr)
