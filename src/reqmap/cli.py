"""Русский CLI для автономного анализа требований и проверки локальной KB."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import traceback

from reqmap import __version__
from reqmap.config import AnalysisProfile, AppConfig, load_config
from reqmap.crosscheck import CrosscheckIssue, crosscheck
from reqmap.crosscheck_deep import crosscheck_deep
from reqmap.deep_aggregation import aggregate_deep_groups
from reqmap.deep_models import DeepRunResult
from reqmap.deep_pipeline import analyze_deep
from reqmap.errors import ReqmapError
from reqmap.export_deep_json import validate_deep_run_result, write_deep_canonical_json
from reqmap.export_deep_markdown import write_deep_markdown
from reqmap.export_deep_xlsx import write_deep_xlsx
from reqmap.export_json import symlink_component, write_canonical_json
from reqmap.export_markdown import write_markdown
from reqmap.export_xlsx import write_xlsx
from reqmap.ids import generated_requirement_id
from reqmap.input_text import load_text
from reqmap.input_xlsx import load_xlsx_bytes
from reqmap.knowledge import load_knowledge
from reqmap.knowledge_v2 import load_knowledge_v2
from reqmap.llm import OpenAICompatibleClient
from reqmap.manifest import (
    RunLogger,
    UnsafeLogError,
    deep_preflight_artifact_bytes,
    write_deep_manifest,
    write_manifest,
)
from reqmap.migrate_v2 import migrate_v1_to_v2
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
_MAX_METADATA_BYTES = 1024 * 1024
_MAX_DIAGNOSTIC_ARTIFACT_BYTES = 4 * 1024 * 1024


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

    if parsed.command == "agent" and parsed.agent_command == "serve":
        from reqmap.agent_config import load_agent_config
        from reqmap.agent_service import AgentService
        from reqmap.mcp_stdio import serve_stdio
        try:
            config = load_agent_config(parsed.config)
            service = AgentService(config)
        except (ReqmapError, OSError, ValueError):
            print("Не удалось запустить агент reqmap; проверьте конфигурацию и права каталогов.",file=sys.stderr)
            return 2
        return serve_stdio(service,sys.stdin.buffer,sys.stdout.buffer,sys.stderr,config.limits)
    if parsed.command == "analyze":
        return _analyze_command(parsed)
    if parsed.command == "knowledge" and parsed.knowledge_command == "validate":
        return _knowledge_validate_command(parsed)
    if parsed.command == "knowledge" and parsed.knowledge_command == "migrate-v1":
        return _knowledge_migrate_command(parsed)
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

    agent = subparsers.add_parser("agent",help="Инструменты агента IDE",add_help=False)
    _localize_parser(agent)
    agent_sub = agent.add_subparsers(dest="agent_command")
    serve = agent_sub.add_parser("serve",help="Запустить MCP stdio",add_help=False)
    _localize_parser(serve)
    serve.add_argument("--config",type=Path,required=True,help="Конфигурация агента без model endpoint")

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
        "--allowed-signers",
        type=Path,
        help="Путь к доверенному allowed_signers для schema v2",
    )
    validate_parser.add_argument(
        "--debug",
        action="store_true",
        help="Показать traceback внутренней ошибки",
    )
    migrate_parser = knowledge_subparsers.add_parser(
        "migrate-v1",
        help="Создать unsigned draft schema v2 из snapshot v1",
        add_help=False,
    )
    _localize_parser(migrate_parser)
    migrate_parser.add_argument(
        "--source",
        required=True,
        type=Path,
        help="Путь к исходному snapshot schema v1",
    )
    migrate_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Каталог нового unsigned draft schema v2",
    )
    migrate_parser.add_argument(
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
    deep_failed_preflight = False
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
        if config.analysis_profile is AnalysisProfile.DEEP:
            run = analyze_deep(request, config, model)
            if type(run) is not DeepRunResult:
                raise TypeError("deep pipeline вернул неканонический DeepRunResult")
            deep_failed_preflight = _is_deep_failed_preflight(run, request)
            if (
                _has_deep_failed_preflight_payload_shape(run)
                and not deep_failed_preflight
            ):
                raise ValueError("deep failed-preflight result неканоничен")
        elif config.analysis_profile is AnalysisProfile.LEGACY:
            run = analyze(request, config, model)
            if type(run) is not RunResult:
                raise TypeError("legacy pipeline вернул неканонический RunResult")
        else:
            raise TypeError("analysis_profile не является canonical AnalysisProfile")
    except Exception as exc:
        _report_exception(
            "Анализ требований завершился внутренней ошибкой.",
            exc,
            debug,
        )
        return 6

    if type(run) is RunResult and run.metadata.get("preflight_ok") is False:
        _print_run_summary(run, AnalysisProfile.LEGACY)
        if run.metadata.get("preflight_artifacts_written") is True:
            _print_artifacts(request.output_dir, _PREFLIGHT_ARTIFACTS)
        return 3
    if type(run) is DeepRunResult and deep_failed_preflight:
        _print_run_summary(run, AnalysisProfile.DEEP)
        if _deep_preflight_artifacts_are_current(run, request.output_dir):
            _print_artifacts(request.output_dir, _PREFLIGHT_ARTIFACTS)
        return 3

    try:
        if type(run) is DeepRunResult:
            issues = _publish_deep_artifacts(run, request.output_dir, config)
        else:
            issues = _publish_artifacts(run, request.output_dir, config)
    except Exception as exc:
        _report_exception(
            "Не удалось создать или проверить выходные артефакты.",
            exc,
            debug,
        )
        unsafe_log = isinstance(exc, UnsafeLogError)
        if type(run) is DeepRunResult:
            if not unsafe_log:
                try:
                    _record_deep_export_failure(run, request.output_dir, config, exc)
                except UnsafeLogError:
                    unsafe_log = True
            profile = AnalysisProfile.DEEP
        else:
            if not unsafe_log:
                try:
                    _record_export_failure(run, request.output_dir, config, exc)
                except UnsafeLogError:
                    unsafe_log = True
            profile = AnalysisProfile.LEGACY
        _print_run_summary(run, profile)
        announced = (
            tuple(
                name
                for name in _FINAL_ARTIFACTS
                if name not in {"run.jsonl", "manifest.json"}
            )
            if unsafe_log
            else _FINAL_ARTIFACTS
        )
        _print_artifacts(request.output_dir, announced)
        return 5

    profile = (
        AnalysisProfile.DEEP
        if type(run) is DeepRunResult
        else AnalysisProfile.LEGACY
    )
    _print_run_summary(run, profile)
    _print_artifacts(request.output_dir, _FINAL_ARTIFACTS)
    if issues:
        for issue in issues:
            print(f"Ошибка {issue.code}: {issue.message_ru}", file=sys.stderr)
        return 5
    return _STATUS_EXIT_CODES[run.run_status]


def _is_deep_failed_preflight(
    run: DeepRunResult,
    request: AnalysisRequest,
) -> bool:
    validate_deep_run_result(run)
    return (
        run.run_status == "FAILED"
        and not run.responsibility_records
        and not run.procedure_graphs
        and not run.evidence
        and tuple(item.requirement for item in run.requirements)
        == request.requirements
        and run.groups == aggregate_deep_groups(run.requirements, ())
        and all(
            item.analysis_state is AnalysisState.SKIPPED
            and item.support_status is None
            and not item.atom_results
            and not item.responsibility_ids
            and not item.procedure_graph_ids
            for item in run.requirements
        )
    )


def _has_deep_failed_preflight_payload_shape(run: DeepRunResult) -> bool:
    return (
        run.run_status == "FAILED"
        and not run.responsibility_records
        and not run.procedure_graphs
        and not run.evidence
        and all(
            item.analysis_state is AnalysisState.SKIPPED
            and item.support_status is None
            and not item.atom_results
            and not item.responsibility_ids
            and not item.procedure_graph_ids
            for item in run.requirements
        )
    )


def _deep_preflight_artifacts_are_current(
    run: DeepRunResult,
    output: Path,
) -> bool:
    try:
        log = _strict_regular_bytes(
            output / "run.jsonl", _MAX_DIAGNOSTIC_ARTIFACT_BYTES
        )
        manifest_bytes = _strict_regular_bytes(
            output / "manifest.json", _MAX_DIAGNOSTIC_ARTIFACT_BYTES
        )
        expected_log, expected_manifest = deep_preflight_artifact_bytes(run)
    except (OSError, UnicodeDecodeError, ValueError, _UsageError):
        return False
    return log == expected_log and manifest_bytes == expected_manifest


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
    from reqmap.source_context import capture_source_document
    source_kind = "xlsx" if arguments.input is not None else "texts" if arguments.requirements is not None else "txt"
    document = capture_source_document(content=raw_input, source_kind=source_kind,
        source_name=requirements[0].coordinate.source_name, requirements=requirements,
        input_profile=config.input_profile if source_kind == "xlsx" else None,
        text_mode=arguments.text_mode if source_kind == "txt" else None)
    return AnalysisRequest(
        requirements=requirements,
        input_sha256=hashlib.sha256(raw_input).hexdigest(),
        input_kind=input_kind,
        source_path=source_path,
        output_dir=arguments.output,
        source_document=document,
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


def _publish_artifacts(run: RunResult, output: Path, config: AppConfig) -> tuple[CrosscheckIssue, ...]:
    from reqmap.publication import publish_artifacts
    return publish_artifacts(run, output, redacted_values=_redacted_values(config))


def _publish_deep_artifacts(run: DeepRunResult, output: Path, config: AppConfig) -> tuple[CrosscheckIssue, ...]:
    from reqmap.publication import publish_artifacts
    return publish_artifacts(run, output, redacted_values=_redacted_values(config))


def _record_export_failure(
    run: RunResult,
    output: Path,
    config: AppConfig,
    error: Exception,
) -> None:
    paths = {name: output / name for name in _FINAL_ARTIFACTS}
    try:
        log_digest = RunLogger(
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
            if name not in {"run.jsonl", "manifest.json"}
            and path.is_file()
            and not path.is_symlink()
        }
        hashes["run.jsonl"] = log_digest
        write_manifest(run, hashes, paths["manifest.json"])
    except UnsafeLogError:
        raise
    except (OSError, ValueError, ReqmapError):
        return


def _record_deep_export_failure(
    run: DeepRunResult,
    output: Path,
    config: AppConfig,
    error: Exception,
) -> None:
    paths = {name: output / name for name in _FINAL_ARTIFACTS}
    try:
        log_digest = RunLogger(
            paths["run.jsonl"],
            redacted_values=_redacted_values(config),
        ).write(
            "export_failed",
            "error",
            "Создание deep-артефактов завершилось ошибкой.",
            error_type=type(error).__name__,
        )
        hashes = {
            name: _sha256_file(path)
            for name, path in paths.items()
            if name not in {"run.jsonl", "manifest.json"}
            and symlink_component(path) is None
            and path.is_file()
            and not path.is_symlink()
        }
        hashes["run.jsonl"] = log_digest
        write_deep_manifest(run, hashes, paths["manifest.json"])
    except UnsafeLogError:
        raise
    except (OSError, ValueError, ReqmapError):
        return


def _redacted_values(config: AppConfig) -> tuple[str, ...]:
    api_key = config.model.api_key
    return () if not api_key else (api_key,)


def _knowledge_validate_command(arguments: argparse.Namespace) -> int:
    try:
        schema_version = _knowledge_schema_version(arguments.path)
        if schema_version == 1:
            knowledge = load_knowledge(arguments.path)
        else:
            if arguments.allowed_signers is None:
                raise _UsageError(
                    "--allowed-signers обязателен для knowledge schema v2."
                )
            knowledge = load_knowledge_v2(
                arguments.path,
                arguments.allowed_signers,
            )
    except Exception as exc:
        _report_exception(
            "Проверка базы знаний завершилась ошибкой.",
            exc,
            bool(arguments.debug),
        )
        return 2
    if schema_version == 1:
        print(
            "База знаний проверена: "
            f"schema=1, release={knowledge.release}, "
            f"components={len(knowledge.components)}, "
            f"capabilities={len(knowledge.capabilities)}, "
            f"evidence={len(knowledge.evidence)}, "
            f"SHA-256={knowledge.snapshot_sha256}."
        )
    else:
        trust = knowledge.trust
        assert trust is not None
        print(
            "База знаний проверена: "
            f"schema=2, release={knowledge.base_release}, "
            f"snapshot={knowledge.snapshot_id}, "
            f"components={len(knowledge.components)}, "
            f"capabilities={len(knowledge.capabilities)}, "
            f"actions={len(knowledge.actions)}, "
            f"evidence={len(knowledge.evidence)}, "
            f"trust=verified, key_id={trust.key_id}, "
            f"manifest_SHA-256={trust.manifest_sha256}."
        )
    return 0


def _knowledge_schema_version(path: Path) -> int:
    payload = _strict_regular_bytes(path / "metadata.json", _MAX_METADATA_BYTES)
    try:
        metadata = _strict_cli_json_object(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _UsageError("metadata.json должен быть строгим UTF-8 JSON object.") from exc
    if "knowledge_schema_version" not in metadata:
        return 1
    marker = metadata["knowledge_schema_version"]
    if type(marker) is not int:
        raise _UsageError("knowledge_schema_version должен быть целым числом.")
    if marker != 2:
        raise _UsageError("knowledge_schema_version не поддерживается.")
    return 2


def _strict_regular_bytes(path: Path, maximum: int) -> bytes:
    if symlink_component(path) is not None:
        raise _UsageError("Путь к metadata или артефакту не может содержать symlink.")
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise _UsageError("Обязательный обычный файл недоступен.") from exc
    if not stat.S_ISREG(before.st_mode):
        raise _UsageError("Ожидался обычный файл.")
    if before.st_size > maximum:
        raise _UsageError("Файл превышает допустимый размер.")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise _UsageError("Ожидался обычный файл.")
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise _UsageError("Файл был заменён во время безопасного чтения.")
        if opened.st_size > maximum:
            raise _UsageError("Файл превышает допустимый размер.")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        finished = os.fstat(descriptor)
        opened_state = (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        finished_state = (
            finished.st_dev,
            finished.st_ino,
            finished.st_mode,
            finished.st_size,
            finished.st_mtime_ns,
            finished.st_ctime_ns,
        )
        if not stat.S_ISREG(finished.st_mode) or finished_state != opened_state:
            raise _UsageError("Файл изменился во время безопасного чтения.")
        if len(payload) > maximum:
            raise _UsageError("Файл превышает допустимый размер.")
        return payload
    finally:
        os.close(descriptor)


def _strict_cli_json_object(source: str) -> dict[str, object]:
    value = json.loads(
        source,
        object_pairs_hook=_cli_unique_json_object,
        parse_constant=_reject_cli_json_constant,
    )
    if type(value) is not dict:
        raise ValueError("Ожидался JSON object")
    return value


def _cli_unique_json_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Повторяющийся JSON key: {key}")
        result[key] = value
    return result


def _reject_cli_json_constant(value: str) -> object:
    raise ValueError(f"Недопустимая JSON-константа: {value}")


def _knowledge_migrate_command(arguments: argparse.Namespace) -> int:
    try:
        report = migrate_v1_to_v2(arguments.source, arguments.output)
    except Exception as exc:
        _report_exception(
            "Миграция базы знаний завершилась ошибкой.",
            exc,
            bool(arguments.debug),
        )
        return 2
    print(
        "Создан unsigned draft schema=2: "
        f"components={report.components}, "
        f"capabilities={report.capabilities}, "
        f"evidence={report.evidence}, "
        f"inferred_actions={report.inferred_actions}, "
        f"destination={report.output_path.resolve()}."
    )
    return 0


def _print_run_summary(
    run: RunResult | DeepRunResult,
    profile: AnalysisProfile,
) -> None:
    print(f"Профиль анализа: {profile.value}")
    print(f"Статус запуска: {run.run_status}")
    incomplete = tuple(
        item.requirement.requirement_id
        for item in run.requirements
        if item.analysis_state is not AnalysisState.COMPLETED
    )
    if incomplete:
        print(f"Незавершённые требования: {', '.join(incomplete)}")
    if type(run) is DeepRunResult:
        counts = _deep_diagnostic_counts(run)
        print(f"procedure_gap: {counts['procedure_gap']}")
        print(f"evidence_conflict: {counts['evidence_conflict']}")


def _deep_diagnostic_counts(run: DeepRunResult) -> dict[str, int]:
    diagnostics = [*run.diagnostics]
    for result in run.requirements:
        diagnostics.extend(result.diagnostics)
        for atom in result.atom_results:
            diagnostics.extend(atom.diagnostics)
    for record in run.responsibility_records:
        diagnostics.extend(record.diagnostics)
    for graph in run.procedure_graphs:
        diagnostics.extend(graph.diagnostics)
    result = {"procedure_gap": 0, "evidence_conflict": 0}
    for diagnostic in diagnostics:
        code = diagnostic.partition(":")[0].strip().casefold()
        if code in result:
            result[code] += 1
    return result


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
