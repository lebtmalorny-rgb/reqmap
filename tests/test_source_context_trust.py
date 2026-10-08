"""Only exact signed map bytes in the context namespace authorize deep context."""
from pathlib import Path
import importlib
import os
import subprocess

import pytest

from reqmap.config import AnalysisProfile, KnowledgeTrustConfig
from reqmap.errors import ReqmapError
from reqmap.export_json import canonical_json_bytes
from reqmap.source_context import capture_source_document
from tests.source_context_support import text_document, map_for


def load(path, doc, profile=AnalysisProfile.LEGACY, trust=None):
    module = importlib.import_module("reqmap.source_context")
    assert hasattr(module, "load_source_context_map"), "safe context loader is missing"
    return module.load_source_context_map(path, doc, profile=profile, trust=trust)


def signed_map(tmp_path, namespace="reqmap-source-context", key_type="ed25519"):
    doc = capture_source_document(**text_document())
    path = tmp_path/"map.json"
    path.write_bytes(canonical_json_bytes(map_for(doc)))
    key = tmp_path/"test-key"
    subprocess.run(["ssh-keygen", "-q", "-t", key_type, "-N", "", "-f", str(key)], check=True, capture_output=True)
    signers = tmp_path/"allowed_signers"
    signers.write_text('context-reviewer namespaces="reqmap-source-context" '+key.with_suffix('.pub').read_text())
    subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", namespace, str(path)], check=True, capture_output=True)
    return doc, path, KnowledgeTrustConfig(signers, "context-reviewer")


@pytest.mark.parametrize("mutation", [None, "namespace", "identity", "bytes", "rsa", "revoked"])
def test_deep_signature_exact_bytes_and_namespace(tmp_path, mutation):
    doc, path, trust = signed_map(tmp_path, namespace="wrong" if mutation == "namespace" else "reqmap-source-context", key_type="rsa" if mutation == "rsa" else "ed25519")
    if mutation == "identity": trust = KnowledgeTrustConfig(trust.allowed_signers_path, "other")
    if mutation == "bytes": path.write_bytes(path.read_bytes()+b' ')
    if mutation == "revoked": trust.allowed_signers_path.write_text("")
    if mutation:
        with pytest.raises(ReqmapError) as error: load(path, doc, AnalysisProfile.DEEP, trust)
        assert error.value.code == "SOURCE_CONTEXT_UNTRUSTED"
    else:
        loaded = load(path, doc, AnalysisProfile.DEEP, trust)
        assert loaded.map_bytes == path.read_bytes()
        assert loaded.signature_bytes == Path(str(path)+'.sig').read_bytes()
        assert loaded.trust.signer_identity == "context-reviewer"


@pytest.mark.parametrize("profile", list(AnalysisProfile))
def test_no_map_is_explicit_and_allowed(profile):
    doc = capture_source_document(**text_document())
    loaded = load(None, doc, profile)
    assert loaded.mapping is None and loaded.map_bytes is None


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory", "missing"])
def test_safe_map_read_and_overlap(tmp_path, kind):
    doc = capture_source_document(**text_document())
    path = tmp_path/'bad'
    if kind == "symlink": path.symlink_to(tmp_path/'elsewhere')
    if kind == "fifo": os.mkfifo(path)
    if kind == "directory": path.mkdir()
    with pytest.raises(ReqmapError) as error: load(path, doc)
    assert error.value.code == "SOURCE_CONTEXT_INVALID"


def test_output_overlap_covers_map_signature_and_trust(tmp_path):
    import reqmap.source_context as module
    assert hasattr(module, 'source_context_output_diagnostics')
    path = tmp_path/'map.json'
    trust = KnowledgeTrustConfig(tmp_path/'trust')
    for output in (tmp_path, path, Path(str(path)+'.sig'), trust.allowed_signers_path):
        assert module.source_context_output_diagnostics(path, (output,), trust)
    assert not module.source_context_output_diagnostics(path, (tmp_path/'outputs',), trust)


def test_changed_map_during_read_is_rejected(tmp_path, monkeypatch):
    doc = capture_source_document(**text_document())
    path = tmp_path/'map.json'
    path.write_bytes(canonical_json_bytes(map_for(doc)))
    original_read = os.read
    changed = False
    def racing_read(fd, maximum):
        nonlocal changed
        result = original_read(fd, maximum)
        if not changed:
            changed = True
            path.write_bytes(path.read_bytes()+b' ')
        return result
    monkeypatch.setattr(os, 'read', racing_read)
    with pytest.raises(ReqmapError) as error: load(path, doc)
    assert error.value.code == 'SOURCE_CONTEXT_INVALID'


@pytest.mark.parametrize('which', ['signature', 'trust'])
def test_signature_and_trust_are_bounded(tmp_path, which):
    doc, path, trust = signed_map(tmp_path)
    target = Path(str(path)+'.sig') if which == 'signature' else trust.allowed_signers_path
    target.write_bytes(b' '*((1024*1024)+1))
    with pytest.raises(ReqmapError) as error: load(path, doc, AnalysisProfile.DEEP, trust)
    assert error.value.code == 'SOURCE_CONTEXT_UNTRUSTED'
