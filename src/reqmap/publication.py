"""Shared artifact publication; callers decide output placement and recovery."""
from pathlib import Path
from reqmap.crosscheck import CrosscheckIssue, crosscheck
from reqmap.crosscheck_deep import crosscheck_deep
from reqmap.deep_models import DeepRunResult
from reqmap.export_json import write_canonical_json
from reqmap.export_xlsx import write_xlsx
from reqmap.export_markdown import write_markdown
from reqmap.export_deep_json import write_deep_canonical_json
from reqmap.export_deep_xlsx import write_deep_xlsx
from reqmap.export_deep_markdown import write_deep_markdown
from reqmap.manifest import RunLogger, write_manifest, write_deep_manifest
from reqmap.models import RunResult


ARTIFACTS = ('result.json','result.xlsx','report.md','run.jsonl','manifest.json')


def publish_artifacts(run: RunResult | DeepRunResult, output: Path, *, redacted_values: tuple[str, ...] = ()) -> tuple[CrosscheckIssue, ...]:
    deep = type(run) is DeepRunResult
    paths = {name:output/name for name in ARTIFACTS}
    logger = RunLogger(paths['run.jsonl'],redacted_values=redacted_values)
    logger.write('analysis_finished','info','Глубокий предметный анализ требований завершён.' if deep else 'Предметный анализ требований завершён.',
                 run_status=run.run_status,requirements_count=len(run.requirements))
    writers = (write_deep_canonical_json,write_deep_xlsx,write_deep_markdown) if deep else (write_canonical_json,write_xlsx,write_markdown)
    hashes = {name:writer(run,paths[name]) for name,writer in zip(ARTIFACTS[:3],writers)}
    issues = (crosscheck_deep if deep else crosscheck)(run,paths['result.json'],paths['result.xlsx'],paths['report.md'])
    if issues:
        digest = logger.write('crosscheck_failed','error','Обнаружены расхождения deep-артефактов.' if deep else 'Обнаружены расхождения выходных артефактов.',issue_codes=[i.code for i in issues])
    else:
        digest = logger.write('artifacts_verified','info','Deep JSON, XLSX и Markdown согласованы с canonical DeepRunResult.' if deep else 'JSON, XLSX и Markdown согласованы с canonical RunResult.')
    hashes['run.jsonl'] = digest
    (write_deep_manifest if deep else write_manifest)(run,hashes,paths['manifest.json'])
    return issues
