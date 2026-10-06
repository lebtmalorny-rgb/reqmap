from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import MappingProxyType

import pytest

from reqmap.errors import ConfigError, ReqmapError
from reqmap.export_json import canonical_json_bytes
from reqmap.models import EvidenceClaimScope
from tests.binding_factories import deep_knowledge, predicate_raw, sign_catalog, write_catalog
from tests.test_mapping import kb


def load(path, knowledge, signers=None):
    from reqmap.binding_catalog import load_binding_catalog
    return load_binding_catalog(path, knowledge, signers)


def test_reviewed_catalog_is_immutable_and_bound_to_snapshot(tmp_path, kb):
    path = write_catalog(tmp_path / "bindings", kb)
    catalog = load(path, kb)
    assert catalog.knowledge_sha256 == kb.snapshot_sha256
    assert catalog.predicates["P-NOVA-CREATE"].action == "create"
    assert catalog.predicates["P-NOVA-CREATE"].evidence_ids == ("E-NOVA",)
    with pytest.raises(TypeError):
        catalog.predicates["invented"] = catalog.predicates["P-NOVA-CREATE"]


def test_unbound_catalog_is_rejected(tmp_path, kb):
    with pytest.raises(ReqmapError, match="BINDING_KNOWLEDGE_MISMATCH"):
        load(write_catalog(tmp_path / "bindings", kb, knowledge_sha256="c" * 64), kb)


def test_context_cannot_back_predicate(tmp_path, kb):
    evidence = replace(kb.evidence["E-NOVA"], claim_scope=EvidenceClaimScope.CONTEXT)
    kb = replace(kb, evidence=MappingProxyType({**kb.evidence, "E-NOVA": evidence}))
    with pytest.raises(ReqmapError, match="EVIDENCE_CONTEXT_ONLY"):
        load(write_catalog(tmp_path / "bindings", kb), kb)


@pytest.mark.parametrize("field,value", [
    ("review_state", "draft"), ("component_ref", "neutron"),
    ("source_sha256s", ["f" * 64]), ("source_ids", ["SRC-neutron"]),
    ("locators", ["invented"]), ("capability_ref", "CAP-NEUTRON"),
    ("polarity", "negative"), ("release_scope", "2024.2"),
    ("direction", "invented"), ("evidence_ids", ["unknown"]),
    ("action_ref", "invented"), ("extra", True),
])
def test_invalid_predicate_cannot_become_admissible(tmp_path, kb, field, value):
    raw = {**predicate_raw(kb), field: value}
    with pytest.raises(ReqmapError):
        load(write_catalog(tmp_path / "bindings", kb, predicates=[raw]), kb)


@pytest.mark.parametrize("mutation", ["path", "symlink", "digest", "duplicate_key", "unknown_manifest", "duplicate_id"])
def test_catalog_integrity_rejects_unsafe_inputs(tmp_path, kb, mutation):
    path = write_catalog(tmp_path / "bindings", kb)
    manifest_path = path / "binding-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    if mutation == "path":
        manifest["files"][0]["path"] = "../predicates.jsonl"
    elif mutation == "symlink":
        data = path / "predicates.jsonl"
        data.rename(tmp_path / "outside")
        data.symlink_to(tmp_path / "outside")
    elif mutation == "digest":
        (path / "predicates.jsonl").write_text("{}\n")
    elif mutation == "duplicate_key":
        manifest_path.write_bytes(manifest_path.read_bytes().replace(b'"catalog_id":', b'"catalog_id":"duplicate","catalog_id":'))
    elif mutation == "unknown_manifest":
        manifest["trust_me"] = True
    else:
        write_catalog(path, kb, predicates=[predicate_raw(kb), predicate_raw(kb)])
    if mutation in {"path", "unknown_manifest"}:
        manifest_path.write_bytes(canonical_json_bytes(manifest))
    with pytest.raises(ReqmapError):
        load(path, kb)


@pytest.mark.parametrize("case", ["valid", "unsigned", "wrong_namespace", "forged", "wrong_relation", "wrong_interface", "internal_trust"])
def test_deep_catalog_preserves_signed_exact_relations(tmp_path, case):
    kb, signers, key = deep_knowledge(tmp_path / "deep")
    raw = predicate_raw(kb)
    if case == "wrong_relation":
        raw["target_ref"] = "TARGET-WRONG"
    if case == "wrong_interface":
        raw["interface"] = "gui"
    path = write_catalog(tmp_path / "bindings", kb, predicates=[raw])
    if case != "unsigned":
        sign_catalog(path, key, "reqmap-snapshot" if case == "wrong_namespace" else "reqmap-obligation-binding")
    if case == "forged":
        (path / "binding-manifest.sig").write_text("forged")
    if case == "internal_trust":
        internal = path / "allowed_signers"
        internal.write_bytes(signers.read_bytes())
        signers = internal
    if case == "valid":
        catalog = load(path, kb, signers)
        assert catalog.signer_identity == "reqmap-snapshot"
        assert catalog.predicates["P-NOVA-CREATE"].action_ref == "ACTION-NOVA-CREATE"
    else:
        with pytest.raises(ReqmapError):
            load(path, kb, signers)


def test_both_configs_resolve_binding_catalog_without_model_changes(tmp_path):
    from reqmap.agent_config import load_agent_config
    from reqmap.config import load_config
    from tests.test_agent_config import config_file
    from tests.test_config import BASE_CONFIG, write_config
    agent = load_agent_config(config_file(tmp_path, binding_catalog_path="bindings"))
    cli = load_config(write_config(tmp_path, {**BASE_CONFIG, "binding_catalog_path": "bindings"}), {})
    assert agent.binding_catalog_path == cli.binding_catalog_path == tmp_path / "bindings"


def test_binding_catalog_is_protected_from_agent_outputs(tmp_path):
    from reqmap.agent_config import load_agent_config
    from tests.test_agent_config import config_file
    with pytest.raises(ConfigError):
        load_agent_config(config_file(tmp_path, binding_catalog_path="outputs/bindings"))


def test_agent_preflight_does_not_ignore_configured_missing_catalog(tmp_path):
    from reqmap.agent_knowledge import load_agent_knowledge
    from tests.test_agent_session import service
    config = replace(service(tmp_path).config, binding_catalog_path=tmp_path / "missing")
    with pytest.raises(ReqmapError):
        load_agent_knowledge(config)
