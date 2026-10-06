"""Explicit synthetic binding inputs; never used in production knowledge."""
from pathlib import Path
import hashlib
import json
import subprocess

from reqmap.export_json import canonical_json_bytes
from reqmap.knowledge_v2 import KnowledgeBaseV2, load_knowledge_v2
from tests.deep_factories import signed_v2_snapshot


def predicate_raw(kb):
    raw = json.loads((Path(__file__).parent / "fixtures/binding_catalog/predicate.json").read_text())
    if isinstance(kb, KnowledgeBaseV2):
        raw.update(evidence_ids=["EV-NOVA-CREATE"], capability_ref="CAP-NOVA-CREATE",
                   action_ref="ACTION-NOVA-CREATE", effect_ref="EFFECT-NOVA-SERVER-ACTIVE",
                   target_ref="TARGET-NOVA-SERVER")
    records = [kb.evidence[eid] for eid in raw["evidence_ids"]]
    raw["source_ids"] = [e.source_id for e in records]
    raw["locators"] = [e.locator for e in records]
    raw["source_sha256s"] = [
        kb.sources[e.source_id].content_sha256 if isinstance(kb, KnowledgeBaseV2)
        else kb.sources[e.source_id].sha256 for e in records
    ]
    return raw


def write_catalog(path, kb, *, predicates=None, **manifest_changes):
    path.mkdir(parents=True, exist_ok=True)
    data = b"".join(canonical_json_bytes(p) for p in (predicates if predicates is not None else [predicate_raw(kb)]))
    (path / "predicates.jsonl").write_bytes(data)
    digest = kb.trust.manifest_sha256 if isinstance(kb, KnowledgeBaseV2) else kb.snapshot_sha256
    manifest = dict(binding_schema_version=1, catalog_id="synthetic-bindings", knowledge_sha256=digest,
                    release_scope="2025.1", files=[dict(path="predicates.jsonl", size=len(data),
                                                       sha256=hashlib.sha256(data).hexdigest())])
    manifest.update(manifest_changes)
    (path / "binding-manifest.json").write_bytes(canonical_json_bytes(manifest))
    return path


def sign_catalog(path, private_key, namespace="reqmap-obligation-binding"):
    manifest = path / "binding-manifest.json"
    signature = path / "binding-manifest.json.sig"
    if signature.exists():
        signature.unlink()
    subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(private_key), "-n", namespace, str(manifest)],
                   check=True, capture_output=True, timeout=10)
    signature.replace(path / "binding-manifest.sig")


def deep_knowledge(tmp_path):
    root, signers = signed_v2_snapshot(tmp_path)
    return load_knowledge_v2(root, signers), signers, signers.parent / "signing_key"


def loaded_catalog(tmp_path, kb):
    from reqmap.binding_catalog import load_binding_catalog
    return load_binding_catalog(write_catalog(tmp_path / "bindings", kb), kb, None)


def binding_case(kb, catalog, proposal, text="Nova должна создавать ВМ через API"):
    from dataclasses import replace
    from reqmap.binding_models import BindingContext
    from reqmap.binding_source import bind_source, canonical_atoms
    from tests.factories import requirement
    binding = bind_source(replace(requirement(), text=text))
    atom = canonical_atoms(binding)[0]
    return atom, BindingContext(binding, catalog), {
        **proposal, "proposal_schema_version": 2, "obligation_id": atom.obligation_id,
        "predicate_ids": list(catalog.predicates),
    }
