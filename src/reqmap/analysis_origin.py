"""Allowlisted, explicitly unattested provenance of external-agent proposals."""
import hashlib
import json
import re
import uuid
from collections.abc import Mapping


_FIELDS = {'mode','session_id','revision','tool_contract_version','workflow_version','proposal_journal_sha256','reported_client','identity_verified'}


def validate_analysis_origin(raw: object) -> dict[str, object]:
    if type(raw) is not dict or not _FIELDS.issubset(raw) or set(raw) - _FIELDS - {'reported_model'}:
        raise ValueError('analysis_origin has invalid fields')
    if raw['mode'] != 'external_agent' or raw['identity_verified'] is not False or raw['reported_client'] not in ('codex','opencode','unknown'):
        raise ValueError('analysis_origin must be external and unattested')
    if type(raw['session_id']) is not str or str(uuid.UUID(raw['session_id'])) != raw['session_id']:
        raise ValueError('invalid origin session_id')
    if type(raw['revision']) is not int or raw['revision'] < 0:
        raise ValueError('invalid origin revision')
    for field in ('tool_contract_version','workflow_version'):
        if raw[field] != '1.0':
            raise ValueError('unsupported origin version')
    if type(raw['proposal_journal_sha256']) is not str or not re.fullmatch('[0-9a-f]{64}',raw['proposal_journal_sha256']):
        raise ValueError('invalid proposal journal hash')
    if 'reported_model' in raw:
        value = raw['reported_model']
        if type(value) is not str or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}',value) is None or '://' in value or value.lower().startswith(('sk-','bearer','token')):
            raise ValueError('reported_model must be an identifier, not a credential or URL')
    return dict(raw)


def origin_metadata(metadata: Mapping) -> dict[str, object] | None:
    if 'analysis_origin' not in metadata:
        return None
    origin = validate_analysis_origin(metadata['analysis_origin'])
    if metadata.get('model') != 'external-agent' or metadata.get('seed') is not None or metadata.get('endpoint_origin') is not None:
        raise ValueError('external-agent metadata cannot claim model control or an endpoint')
    return origin


def origin_text(raw) -> str:
    return json.dumps(validate_analysis_origin(raw),ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def proposal_journal_sha256(record) -> str:
    from reqmap.export_json import canonical_json_bytes
    from reqmap.models import to_dict
    proposals = [to_dict(e) for e in record.events if e.operation in ('reqmap_submit_atoms','reqmap_submit_mapping')]
    return hashlib.sha256(canonical_json_bytes(proposals)).hexdigest()


def build_analysis_origin(record, revision: int) -> dict[str, object]:
    value = dict(mode='external_agent',session_id=record.session_id,revision=revision,
        tool_contract_version=record.seed.settings.tool_contract_version,workflow_version=record.seed.settings.workflow_version,
        proposal_journal_sha256=proposal_journal_sha256(record),reported_client=record.seed.reported_client,identity_verified=False)
    reported = [e.arguments.get('reported_model') for e in record.events if e.accepted and e.operation in ('reqmap_submit_atoms','reqmap_submit_mapping')]
    if reported and all(model == reported[0] and model is not None for model in reported):
        value['reported_model'] = reported[0]
    return validate_analysis_origin(value)
