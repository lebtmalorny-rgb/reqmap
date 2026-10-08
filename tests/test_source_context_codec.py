"""Frozen replay must rederive decisions and reauthorize the current deep signer."""
from copy import deepcopy
from pathlib import Path
import importlib

import pytest

from reqmap.config import AnalysisProfile
from reqmap.errors import ReqmapError
from reqmap.export_json import canonical_json_bytes
from reqmap.source_context import capture_source_document, load_source_context_map
from tests.source_context_support import text_document, map_for, link_for
from tests.test_source_context_trust import signed_map


def freeze(doc, path, profile=AnalysisProfile.LEGACY, trust=None):
    module=importlib.import_module('reqmap.source_context')
    assert hasattr(module,'freeze_source_context'), 'frozen snapshot API is missing'
    loaded=load_source_context_map(path,doc,profile=profile,trust=trust)
    return module.freeze_source_context(doc,loaded)


@pytest.mark.parametrize('profile',list(AnalysisProfile))
def test_frozen_context_roundtrip_without_external_files(tmp_path,profile):
    import reqmap.source_context_codec as codec
    if profile is AnalysisProfile.DEEP:
        doc,path,trust=signed_map(tmp_path)
    else:
        doc=capture_source_document(**text_document())
        path=tmp_path/'map.json';trust=None
        path.write_bytes(canonical_json_bytes(map_for(doc,reviewed=False)))
    frozen=freeze(doc,path,profile,trust)
    path.unlink()
    if profile is AnalysisProfile.DEEP: Path(str(path)+'.sig').unlink()
    raw=codec.encode_source_context_snapshot(frozen)
    restored=codec.decode_source_context_snapshot(raw,profile=profile,trust=trust)
    assert restored==frozen
    assert codec.inspect_source_context_record(raw)==frozen


@pytest.mark.parametrize('field', ['quote','value','entry','bytes','rows','effective','map','extra','base64','trust'])
def test_replay_detects_tampered_decision_even_when_status_unchanged(tmp_path,field):
    import reqmap.source_context_codec as codec
    doc=capture_source_document(**text_document(('Nova должна создавать ВМ','GUI')))
    path=tmp_path/'map.json'
    path.write_bytes(canonical_json_bytes(map_for(doc,disposition='linked',links=(link_for(doc),))))
    frozen=freeze(doc,path)
    raw=codec.encode_source_context_snapshot(frozen)
    if field=='quote':raw['decisions'][0]['applied_links'][0]['origin']['span']['quote']='API'
    if field=='value':raw['decisions'][0]['applied_links'][0]['value']='api'
    if field=='entry':raw['decisions'][0]['entry_sha256']='0'*64
    if field=='bytes':raw['document']['content']='W10K'
    if field=='rows':raw['document']['requirements'].reverse()
    if field=='effective':raw['decisions'][0]['effective_obligations'][0]['obligation']['interface']='api'
    if field=='map':raw['loaded']['mapping']['rows'][0]['context_complete']=False
    if field=='extra':raw['reviewed']=True
    if field=='base64':raw['document']['content']+='\n'
    if field=='trust':raw['loaded']['trust']['signer_identity']='fake'
    with pytest.raises(ReqmapError) as error:codec.decode_source_context_snapshot(raw,profile=AnalysisProfile.LEGACY,trust=None)
    assert error.value.code=='SOURCE_CONTEXT_CHANGED'


def test_replay_rechecks_revoked_signer(tmp_path):
    import reqmap.source_context_codec as codec
    doc,path,trust=signed_map(tmp_path)
    frozen=freeze(doc,path,AnalysisProfile.DEEP,trust)
    raw=codec.encode_source_context_snapshot(frozen)
    with trust.allowed_signers_path.open('a') as file:file.write('# unrelated trust entry\n')
    assert codec.decode_source_context_snapshot(raw,profile=AnalysisProfile.DEEP,trust=trust)==frozen
    trust.allowed_signers_path.write_text('')
    assert codec.inspect_source_context_record(raw)==frozen  # integrity is not authorization
    with pytest.raises(ReqmapError) as error:codec.decode_source_context_snapshot(raw,profile=AnalysisProfile.DEEP,trust=trust)
    assert error.value.code=='SOURCE_CONTEXT_UNTRUSTED'


def test_contract_change_is_explicit_not_a_reinterpreted_decision(tmp_path,monkeypatch):
    import reqmap.source_context as module
    import reqmap.source_context_codec as codec
    doc=capture_source_document(**text_document())
    frozen=freeze(doc,None)
    raw=codec.encode_source_context_snapshot(frozen)
    monkeypatch.setattr(module,'source_context_resolver_sha256',lambda:'0'*64)
    with pytest.raises(ReqmapError) as error:codec.decode_source_context_snapshot(raw,profile=AnalysisProfile.LEGACY,trust=None)
    assert error.value.code=='SOURCE_CONTEXT_CONTRACT_MISMATCH'


def test_replay_resolver_file_hashing_does_not_scale_with_row_count(tmp_path,monkeypatch):
    import reqmap.source_context as module
    import reqmap.source_context_codec as codec
    doc=capture_source_document(**text_document(tuple('Требование '+str(i) for i in range(30))))
    frozen=freeze(doc,None)
    raw=codec.encode_source_context_snapshot(frozen)
    actual=module.source_context_resolver_sha256
    calls=[]
    def counted():
        calls.append(1)
        return actual()
    monkeypatch.setattr(module,'source_context_resolver_sha256',counted)
    assert codec.inspect_source_context_record(raw)==frozen
    assert len(calls)<=2, 'code manifest must be read per snapshot, not per row'
