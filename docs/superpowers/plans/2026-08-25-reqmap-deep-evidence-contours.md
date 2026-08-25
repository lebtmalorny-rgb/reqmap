# Reqmap Deep Evidence Contours Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в `reqmap` параллельный deep-профиль с knowledge schema v2, подписанным snapshot, раздельными контурами OpenStack/Kolla-Ansible/host OS и проверяемыми процедурами, не изменяя поведение legacy-профиля по умолчанию.

**Architecture:** Legacy v1 остаётся на существующих `models.py`, `knowledge.py`, `mapping.py`, `pipeline.py` и exporters. Deep v2 реализуется отдельными сфокусированными модулями и включается только через `analysis_profile=deep`; CLI выбирает pipeline после строгого preflight. Полнотекстовый corpus возвращает только source candidates, а финальные статусы разрешены исключительно нормализованным и проверенным evidence.

**Tech Stack:** Python 3.11+, dataclasses/enums, JSON/JSONL, `ssh-keygen -Y sign/-Y verify` с Ed25519, `openpyxl==3.1.5`, OpenAI-compatible Chat Completions, pytest 8.3.5, Hypothesis 6.131.9.

**Spec:** `docs/superpowers/specs/2026-08-25-reqmap-deep-evidence-contours-design.md`

## Global Constraints

- Текущий legacy-профиль и output schema `1.0` остаются default behavior; старые конфигурации без `analysis_profile` продолжают работать.
- Deep-профиль принимает только knowledge schema `2`, `snapshot_status=approved` и корректную Ed25519-подпись доверенного manifest.
- Базовый release: upstream OpenStack `2025.1`; `target_release=2026.1` разрешён только для `lifecycle_phase=upgrade`.
- Единственный host profile первого цикла: `rocky_linux_9`.
- Runtime не открывает `source_url` и не обращается к внешнему embedding/vector service; разрешён только настроенный OpenAI-compatible model endpoint.
- `supported` требует direct positive evidence; `not_supported` требует direct negative evidence или доказанную version incompatibility; отсутствие evidence означает `insufficient_evidence`.
- Одна `ResponsibilityRecord` содержит ровно один contour: `openstack_runtime`, `kolla_ansible` или `host_os`.
- Kolla-driven host change обязан создавать отдельные связанные записи Kolla-Ansible и host OS с раздельными `executor_ref` и `target_ref`.
- Все procedural actions, verify и rollback steps имеют evidence либо явный gap.
- Секреты, private signing keys и полный source corpus не коммитятся.
- Первый этап создаёт архитектуру, synthetic/minimal v2 fixtures и gold set; production deep snapshot наполняется следующими этапами.
- Каждый task выполняется через RED → GREEN → focused regression → commit; существующий полный suite должен остаться зелёным.

## File Structure

Новые runtime-модули:

- `src/reqmap/deep_models.py` — стабильные enums и canonical dataclasses output schema v2.
- `src/reqmap/snapshot_trust.py` — manifest, hashes, Ed25519/OpenSSH verification и trust metadata.
- `src/reqmap/knowledge_v2.py` — schema v2 records, strict loader и referential/version validation.
- `src/reqmap/migrate_v2.py` — безопасная миграция v1 snapshot в unsigned draft v2.
- `src/reqmap/deep_retrieval.py` — deterministic BM25 normalized retrieval и corpus discovery interface.
- `src/reqmap/deep_mapping.py` — strict LLM response, evidence gate и `ResponsibilityRecord`.
- `src/reqmap/procedure.py` — инстанцирование и validation procedure DAG.
- `src/reqmap/deep_aggregation.py` — atom/requirement/group/run aggregation для deep diagnostics.
- `src/reqmap/deep_pipeline.py` — deep preflight, orchestration, checkpoint и resume.
- `src/reqmap/export_deep_json.py` — canonical deep JSON и structural validator.
- `src/reqmap/export_deep_xlsx.py` — deep workbook.
- `src/reqmap/export_deep_markdown.py` — deep Russian report.
- `src/reqmap/crosscheck_deep.py` — cross-artifact proof для deep output.

Изменяемые общие модули:

- `src/reqmap/config.py` — additive `analysis_profile` и `knowledge_trust`.
- `src/reqmap/ids.py` — stable deep IDs.
- `src/reqmap/prompts.py` — versioned deep responsibility prompt.
- `src/reqmap/manifest.py` — additive deep run manifest writer без изменения v1 writer.
- `src/reqmap/cli.py` — dispatch legacy/deep и maintenance subcommands.

Maintenance tools:

- `tools/kb/build_snapshot_v2.py` — canonical file manifest.
- `tools/kb/sign_snapshot_v2.py` — Ed25519/OpenSSH detached signature.

Test support:

- `tests/deep_factories.py` — canonical v2 objects и temporary signed snapshot builder.
- `tests/fixtures/deep_gold.json` — frozen cases без private key.
- отдельные `tests/test_*_v2.py` и `tests/test_deep_acceptance.py` по задачам ниже.

Документация:

- `config.deep.example.yaml`;
- обновления `README.md`, `INSTALL_OFFLINE.md`, `RUNBOOK.md`, `KNOWLEDGE_BASE.md`, `OUTPUT_SCHEMA.md`, `TROUBLESHOOTING.md`, `CLIENTS_CODEX_OPENCODE.md`.

## Spec Coverage Map

| Spec sections | Implementation tasks |
|---|---|
| 1–5: назначение, contours, canonical result | 1, 2, 9, 10, 11 |
| 6: knowledge schema v2 | 3, 4, 5, 6, 7 |
| 7: source/evidence contract | 4, 5, 8, 9 |
| 8: signed offline trust | 2, 3, 6, 13, 16 |
| 9: retrieval, evidence gate, procedure pipeline | 8, 9, 10, 13 |
| 10: diagnostics and run states | 9, 10, 11, 12, 13 |
| 11: five output artifacts | 12, 14, 15 |
| 12: legacy compatibility and migration | 2, 7, 13, 15 |
| 13: deterministic tests, gold set, model evaluation boundary | 1–16, final gate |
| 14–16: phase boundary, sequencing, completion criteria | 16 and final gate |

---

### Task 1: Canonical deep models and stable IDs

**Files:**
- Create: `src/reqmap/deep_models.py`
- Modify: `src/reqmap/ids.py`
- Create: `tests/deep_factories.py`
- Create: `tests/test_deep_models.py`
- Modify: `tests/test_models.py`

**Interfaces:**
- Consumes: `Requirement`, `AtomicClaim`, `AnalysisState`, `SupportStatus`, `EvidencePolarity`, `EvidenceStrength` из `reqmap.models`.
- Produces: `ResponsibilityContour`, `LifecyclePhase`, `VersionScope`, `DeepEvidence`, `ResponsibilityRecord`, `ProcedureStep`, `ProcedureGraph`, `DeepAtomResult`, `DeepRequirementResult`, `DeepGroupResult`, `DeepRunResult`; `responsibility_id()`, `procedure_graph_id()`, `procedure_step_id()`.

- [ ] **Step 1: Write failing enum, ID and serialization tests**

```python
def test_deep_records_serialize_with_stable_ids() -> None:
    scope = VersionScope("2025.1", "2025.1", "2025.1", "rocky_linux_9", "2025.1")
    record = responsibility("REQ-0001-A001", 1, scope=scope)

    assert record.record_id == "REQ-0001-A001-R001"
    assert to_dict(record)["contour"] == "openstack_runtime"
    assert responsibility_id("REQ-0001-A001", 2) == "REQ-0001-A001-R002"
    assert procedure_graph_id("REQ-0001", 1) == "REQ-0001-P001"
    assert procedure_step_id("REQ-0001-P001", 3) == "REQ-0001-P001-S003"


def test_version_scope_rejects_2026_outside_upgrade() -> None:
    with pytest.raises(ValueError, match="2026.1.*upgrade"):
        validate_version_scope(
            VersionScope("2025.1", "2026.1", "2025.1", "rocky_linux_9", "2025.1 -> 2026.1"),
            LifecyclePhase.RUNTIME,
        )
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_deep_models.py tests/test_models.py`

Expected: FAIL because `reqmap.deep_models` and deep ID helpers do not exist.

- [ ] **Step 3: Implement exact enums and dataclasses**

```python
class ResponsibilityContour(str, Enum):
    OPENSTACK_RUNTIME = "openstack_runtime"
    KOLLA_ANSIBLE = "kolla_ansible"
    HOST_OS = "host_os"


class LifecyclePhase(str, Enum):
    PREFLIGHT = "preflight"
    DEPLOY = "deploy"
    RUNTIME = "runtime"
    RECONFIGURE = "reconfigure"
    UPGRADE = "upgrade"
    MIGRATE = "migrate"
    RECOVER = "recover"
    VERIFY = "verify"
    ROLLBACK = "rollback"


@dataclass(frozen=True)
class VersionScope:
    source_release: str
    target_release: str
    kolla_ansible_release: str
    host_profile: str
    version_constraint: str


@dataclass(frozen=True)
class ResponsibilityRecord:
    record_id: str
    requirement_id: str
    atomic_claim_id: str
    contour: ResponsibilityContour
    component_ref: str
    executor_ref: str
    target_contour: ResponsibilityContour
    target_ref: str
    action_ref: str | None
    effect_ref: str | None
    lifecycle_phase: LifecyclePhase
    version_scope: VersionScope
    evidence_ids: tuple[str, ...]
    support_status: SupportStatus
    related_record_ids: tuple[str, ...] = ()
    procedure_step_ids: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeepEvidence:
    evidence_id: str
    claim: str
    claim_kind: str
    polarity: EvidencePolarity
    strength: EvidenceStrength
    source_id: str
    locator: str
    version_constraint: str
    applicable_contours: tuple[ResponsibilityContour, ...]
    supports_entity_refs: tuple[str, ...]
    local_excerpt: str
    review_state: str


@dataclass(frozen=True)
class ProcedureStep:
    step_id: str
    phase: LifecyclePhase
    contour: ResponsibilityContour
    executor_ref: str
    target_ref: str
    action_ref: str
    preconditions: tuple[str, ...]
    success_criteria: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    depends_on: tuple[str, ...] = ()
    rollback_step_id: str | None = None


@dataclass(frozen=True)
class ProcedureGraph:
    graph_id: str
    requirement_id: str
    template_id: str
    steps: tuple[ProcedureStep, ...]
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeepAtomResult:
    atom: AtomicClaim
    analysis_state: AnalysisState
    support_status: SupportStatus | None
    responsibility_ids: tuple[str, ...]
    supported_aspects: tuple[str, ...] = ()
    unconfirmed_aspects: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeepRequirementResult:
    requirement: Requirement
    analysis_state: AnalysisState
    support_status: SupportStatus | None
    atom_results: tuple[DeepAtomResult, ...]
    responsibility_ids: tuple[str, ...]
    procedure_graph_ids: tuple[str, ...]
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeepGroupResult:
    group_id: str
    source_requirement_ids: tuple[str, ...]
    support_status: SupportStatus | None
    component_refs: tuple[str, ...]
    responsibility_ids: tuple[str, ...]
    analysis_states: tuple[AnalysisState, ...]


@dataclass(frozen=True)
class DeepRunResult:
    run_id: str
    schema_version: str
    run_status: str
    requirements: tuple[DeepRequirementResult, ...]
    groups: tuple[DeepGroupResult, ...]
    responsibility_records: tuple[ResponsibilityRecord, ...]
    procedure_graphs: tuple[ProcedureGraph, ...]
    evidence: tuple[DeepEvidence, ...]
    metadata: Mapping[str, object]
    diagnostics: tuple[str, ...] = ()
```

Validate `DeepRunResult.schema_version == "2.0"`; deep atom and requirement results reference responsibility IDs rather than duplicating records. Put reusable canonical constructors such as `responsibility()`, `deep_evidence()` and `deep_run()` in `tests/deep_factories.py`.

- [ ] **Step 4: Add stable ID helpers and strict value validation**

```python
def responsibility_id(atom_identifier: str, ordinal: int) -> str:
    return f"{atom_identifier}-R{ordinal:03d}"


def procedure_graph_id(requirement_identifier: str, ordinal: int) -> str:
    return f"{requirement_identifier}-P{ordinal:03d}"


def procedure_step_id(graph_identifier: str, ordinal: int) -> str:
    return f"{graph_identifier}-S{ordinal:03d}"
```

Reject non-positive ordinals, unsafe IDs, empty references, mixed contour strings, unsupported release values and `host_profile != rocky_linux_9`.

- [ ] **Step 5: Run focused tests and legacy model regression**

Run: `.venv/bin/python -m pytest -q tests/test_deep_models.py tests/test_models.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/reqmap/deep_models.py src/reqmap/ids.py tests/deep_factories.py tests/test_deep_models.py tests/test_models.py
git commit -m "feat: add canonical deep analysis models"
```

---

### Task 2: Additive deep configuration profile

**Files:**
- Modify: `src/reqmap/config.py`
- Modify: `config.example.yaml`
- Create: `config.deep.example.yaml`
- Modify: `tests/test_config.py`

**Interfaces:**
- Consumes: existing `load_config(path, environ) -> AppConfig`.
- Produces: `AnalysisProfile`, `KnowledgeTrustConfig`; extended `AppConfig.analysis_profile` and `AppConfig.knowledge_trust` with legacy-safe defaults.

- [ ] **Step 1: Write failing legacy/default and deep config tests**

```python
def test_config_without_profile_remains_legacy(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, BASE_CONFIG), {})
    assert config.analysis_profile is AnalysisProfile.LEGACY
    assert config.knowledge_trust is None


def test_deep_config_resolves_allowed_signers_relative_to_config(tmp_path: Path) -> None:
    config = load_config(
        write_config(tmp_path, {
            **BASE_CONFIG,
            "analysis_profile": "deep",
            "knowledge_trust": {"allowed_signers_path": "trust/allowed_signers"},
        }),
        {},
    )
    assert config.analysis_profile is AnalysisProfile.DEEP
    assert config.knowledge_trust.allowed_signers_path == tmp_path / "trust/allowed_signers"


def test_deep_config_requires_trust(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="knowledge_trust"):
        load_config(write_config(tmp_path, {**BASE_CONFIG, "analysis_profile": "deep"}), {})
```

- [ ] **Step 2: Run config tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_config.py`

Expected: FAIL on unknown `analysis_profile`/`knowledge_trust`.

- [ ] **Step 3: Extend config without changing positional legacy constructors**

```python
class AnalysisProfile(str, Enum):
    LEGACY = "legacy"
    DEEP = "deep"


@dataclass(frozen=True)
class KnowledgeTrustConfig:
    allowed_signers_path: Path
    signer_identity: str = "reqmap-snapshot"


@dataclass(frozen=True)
class AppConfig:
    model: ModelConfig
    knowledge_path: Path
    input_profile: InputProfile | None
    top_k: int
    analysis_profile: AnalysisProfile = AnalysisProfile.LEGACY
    knowledge_trust: KnowledgeTrustConfig | None = None
```

Allow only top-level keys `model`, `knowledge_path`, `input_profile`, `top_k`, `analysis_profile`, `knowledge_trust`. Reject trust objects with unknown fields, blank identities, non-string paths or a missing trust object in deep mode.

- [ ] **Step 4: Add safe examples**

`config.example.yaml` remains legacy. `config.deep.example.yaml` contains:

```json
{
  "analysis_profile": "deep",
  "model": {"base_url": "http://127.0.0.1:8000/v1", "model": "local-model"},
  "knowledge_path": "knowledge/epoxy-2025.1-deep",
  "knowledge_trust": {"allowed_signers_path": "trust/allowed_signers"},
  "top_k": 12
}
```

- [ ] **Step 5: Run config and documentation-language tests**

Run: `.venv/bin/python -m pytest -q tests/test_config.py tests/test_docs_language.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/reqmap/config.py config.example.yaml config.deep.example.yaml tests/test_config.py
git commit -m "feat: configure legacy and deep analysis profiles"
```

---

### Task 3: Snapshot trust and Ed25519 verification

**Files:**
- Create: `src/reqmap/snapshot_trust.py`
- Create: `tests/test_snapshot_trust.py`
- Modify: `tests/deep_factories.py`

**Interfaces:**
- Consumes: snapshot root, external OpenSSH `allowed_signers` path and fixed namespace `reqmap-snapshot`.
- Produces: `SnapshotTrustError(ReqmapError)`, `SnapshotFile`, `SnapshotTrust`; `build_snapshot_manifest()`, `parse_snapshot_manifest()`, `verify_snapshot_integrity()`, `verify_snapshot_signature()` and `verify_snapshot()`.

- [ ] **Step 1: Write failing signature, tamper and trust-boundary tests**

```python
def test_verify_snapshot_accepts_ephemeral_ed25519_signature(tmp_path: Path) -> None:
    root, allowed_signers = signed_v2_snapshot(tmp_path)
    trust = verify_snapshot(root, allowed_signers)
    assert trust.signer_identity == "reqmap-snapshot"
    assert len(trust.manifest_sha256) == 64


def test_verify_snapshot_rejects_tampered_file(tmp_path: Path) -> None:
    root, allowed_signers = signed_v2_snapshot(tmp_path)
    (root / "components.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(SnapshotTrustError, match="SNAPSHOT_INTEGRITY_FAILED"):
        verify_snapshot(root, allowed_signers)


def test_verify_snapshot_rejects_signer_file_inside_snapshot(tmp_path: Path) -> None:
    root, _ = signed_v2_snapshot(tmp_path)
    with pytest.raises(SnapshotTrustError, match="вне snapshot"):
        verify_snapshot(root, root / "allowed_signers")
```

- [ ] **Step 2: Run trust tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_snapshot_trust.py`

Expected: FAIL because the trust module does not exist.

- [ ] **Step 3: Implement strict manifest parser and hash verification**

Manifest schema:

```json
{
  "manifest_schema_version": "1.0",
  "knowledge_schema_version": 2,
  "snapshot_id": "epoxy-2025.1-deep-001",
  "key_id": "reqmap-maintenance-2026",
  "files": [
    {"path": "components.json", "size": 123, "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
  ]
}
```

Reject duplicate paths, absolute paths, `..`, symlinks, missing files, size/hash mismatch, unknown manifest fields and unlisted `.json`, `.jsonl`, `.md` or index files under the snapshot root. Exclude only `snapshot-manifest.json` and `snapshot-manifest.sig` from the file list.

Use these immutable result types:

```python
@dataclass(frozen=True)
class SnapshotFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class SnapshotTrust:
    snapshot_id: str
    key_id: str
    signer_identity: str
    manifest_sha256: str
    files: tuple[SnapshotFile, ...]
```

`build_snapshot_manifest(root)` walks the same allowlisted file set that verification enforces and returns a sorted canonical object used by both tests and maintenance tools.

- [ ] **Step 4: Verify Ed25519 with fixed OpenSSH arguments**

```python
completed = subprocess.run(
    [
        "ssh-keygen", "-Y", "verify",
        "-f", str(allowed_signers_path),
        "-I", "reqmap-snapshot",
        "-n", "reqmap-snapshot",
        "-s", str(root / "snapshot-manifest.sig"),
    ],
    input=(root / "snapshot-manifest.json").read_bytes(),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    timeout=10,
    check=False,
)
```

Use `shell=False`; never include signature stderr in user diagnostics. Map missing `ssh-keygen` to `SNAPSHOT_VERIFIER_MISSING`, timeout to `SNAPSHOT_SIGNATURE_TIMEOUT`, non-zero exit to `SNAPSHOT_UNTRUSTED`.

- [ ] **Step 5: Generate test keys only inside `tmp_path`**

`signed_v2_snapshot()` runs `ssh-keygen -q -t ed25519 -N "" -f str(tmp_path / "trust/signing_key")`, writes identity `reqmap-snapshot` plus the generated public-key line to `tmp_path / "trust/allowed_signers"`, and signs the canonical manifest. Never add the generated private key to Git.

- [ ] **Step 6: Run focused security tests**

Run: `.venv/bin/python -m pytest -q tests/test_snapshot_trust.py`

Expected: PASS, including signature mismatch, wrong identity, wrong namespace, symlink and tamper cases.

- [ ] **Step 7: Commit**

```bash
git add src/reqmap/snapshot_trust.py tests/test_snapshot_trust.py tests/deep_factories.py
git commit -m "feat: verify signed deep knowledge snapshots"
```

---

### Task 4: Knowledge schema v2 strict loader

**Files:**
- Create: `src/reqmap/knowledge_v2.py`
- Create: `tests/test_knowledge_v2.py`
- Create: `tests/fixtures/kb_v2_minimal/metadata.json`
- Create: `tests/fixtures/kb_v2_minimal/components.json`
- Create: `tests/fixtures/kb_v2_minimal/actors.json`
- Create: `tests/fixtures/kb_v2_minimal/targets.jsonl`
- Create: `tests/fixtures/kb_v2_minimal/capabilities.jsonl`
- Create: `tests/fixtures/kb_v2_minimal/actions.jsonl`
- Create: `tests/fixtures/kb_v2_minimal/effects.jsonl`
- Create: `tests/fixtures/kb_v2_minimal/evidence.jsonl`
- Create: `tests/fixtures/kb_v2_minimal/procedures.jsonl`
- Create: `tests/fixtures/kb_v2_minimal/synonyms.json`
- Create: `tests/fixtures/kb_v2_minimal/source-manifest.json`
- Create: `tests/fixtures/kb_v2_minimal/corpus/SRC-MINIMAL.md`
- Modify: `tests/deep_factories.py`

**Interfaces:**
- Consumes: `verify_snapshot()` and the v2 files defined by the spec.
- Produces: `KnowledgeV2Error(ReqmapError)`, `ActorRecord`, `TargetRecord`, `DeepComponentRecord`, `DeepCapabilityRecord`, `ActionRecord`, `EffectRecord`, `ProcedureTemplateRecord`, `SourceArtifactRecord`, `KnowledgeBaseV2`; `load_knowledge_v2()` and `load_knowledge_v2_for_maintenance()`.

- [ ] **Step 1: Write failing complete-load and strict-schema tests**

```python
def test_load_knowledge_v2_returns_immutable_verified_graph(tmp_path: Path) -> None:
    root, allowed_signers = signed_v2_snapshot(tmp_path)
    kb = load_knowledge_v2(root, allowed_signers)
    assert kb.schema_version == 2
    assert kb.snapshot_status == "approved"
    assert kb.host_profile == "rocky_linux_9"
    assert kb.trust.manifest_sha256
    with pytest.raises(TypeError):
        kb.actions["other"] = kb.actions["ACTION-NOVA-CREATE"]


@pytest.mark.parametrize("filename", V2_REQUIRED_FILES)
def test_load_knowledge_v2_rejects_missing_required_file(tmp_path: Path, filename: str) -> None:
    root, allowed_signers = signed_v2_snapshot(tmp_path)
    (root / filename).unlink()
    with pytest.raises((KnowledgeV2Error, SnapshotTrustError)):
        load_knowledge_v2(root, allowed_signers)
```

- [ ] **Step 2: Run loader tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_knowledge_v2.py`

Expected: FAIL because `knowledge_v2.py` does not exist.

- [ ] **Step 3: Implement records and exact file parsing**

```python
@dataclass(frozen=True)
class KnowledgeBaseV2:
    root: Path
    schema_version: int
    snapshot_id: str
    snapshot_status: str
    base_release: str
    upgrade_target: str
    kolla_ansible_release: str
    host_profile: str
    components: Mapping[str, DeepComponentRecord]
    actors: Mapping[str, ActorRecord]
    targets: Mapping[str, TargetRecord]
    capabilities: Mapping[str, DeepCapabilityRecord]
    actions: Mapping[str, ActionRecord]
    effects: Mapping[str, EffectRecord]
    evidence: Mapping[str, DeepEvidence]
    procedures: Mapping[str, ProcedureTemplateRecord]
    sources: Mapping[str, SourceArtifactRecord]
    synonyms: Mapping[str, tuple[str, ...]]
    trust: SnapshotTrust | None
```

Use the strict JSON helpers pattern from `knowledge.py`: reject unknown fields, duplicate JSON keys, non-built-in scalar types, duplicate IDs, unsafe relative paths and blank strings.

Define the remaining records with these exact fields:

```python
@dataclass(frozen=True)
class DeepComponentRecord:
    component_id: str
    display_name: str
    kind: str
    releases: tuple[str, ...]


@dataclass(frozen=True)
class ActorRecord:
    actor_id: str
    kind: str
    component_ref: str | None


@dataclass(frozen=True)
class TargetRecord:
    target_id: str
    contour: ResponsibilityContour
    component_ref: str | None
    host_profile: str | None


@dataclass(frozen=True)
class DeepCapabilityRecord:
    capability_id: str
    component_ref: str
    name_ru: str
    terms: tuple[str, ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ActionRecord:
    action_id: str
    component_ref: str
    contour: ResponsibilityContour
    interface_type: str
    operation: str
    target_ref: str
    effect_refs: tuple[str, ...]
    version_scope: VersionScope
    evidence_ids: tuple[str, ...]
    procedure_required: bool


@dataclass(frozen=True)
class EffectRecord:
    effect_id: str
    target_ref: str
    before_state: str
    after_state: str
    verification_criteria: tuple[str, ...]
    reversible: bool
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ProcedureTemplateStepRecord:
    local_step_id: str
    phase: LifecyclePhase
    contour: ResponsibilityContour
    executor_ref: str
    target_ref: str
    action_ref: str
    preconditions: tuple[str, ...]
    success_criteria: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    depends_on: tuple[str, ...]
    rollback_step_local_id: str | None


@dataclass(frozen=True)
class ProcedureTemplateRecord:
    template_id: str
    lifecycle_phase: LifecyclePhase
    action_refs: tuple[str, ...]
    steps: tuple[ProcedureTemplateStepRecord, ...]


@dataclass(frozen=True)
class SourceArtifactRecord:
    source_id: str
    source_type: str
    project: str
    release: str
    git_tag: str | None
    git_commit: str | None
    source_url: str | None
    retrieved_at: str
    local_path: str
    content_sha256: str
    provenance: str
```

- [ ] **Step 4: Separate runtime and maintenance entrypoints**

```python
def load_knowledge_v2(path: Path, allowed_signers_path: Path) -> KnowledgeBaseV2:
    trust = verify_snapshot(path, allowed_signers_path)
    kb = _load_v2_records(path, trust)
    if kb.snapshot_status != "approved":
        raise KnowledgeV2Error("SNAPSHOT_NOT_APPROVED", "Deep snapshot не утверждён.")
    return _validated(kb)


def load_knowledge_v2_for_maintenance(path: Path) -> KnowledgeBaseV2:
    return _validated(_load_v2_records(path, trust=None), allow_draft=True)
```

The runtime function must never expose a `verify_signature=False` escape hatch.

- [ ] **Step 5: Run loader tests**

Run: `.venv/bin/python -m pytest -q tests/test_knowledge_v2.py tests/test_snapshot_trust.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/reqmap/knowledge_v2.py tests/test_knowledge_v2.py tests/fixtures/kb_v2_minimal tests/deep_factories.py
git commit -m "feat: load knowledge schema v2"
```

---

### Task 5: V2 referential, evidence and version validation

**Files:**
- Modify: `src/reqmap/knowledge_v2.py`
- Create: `tests/test_knowledge_v2_validation.py`
- Modify: `tests/deep_factories.py`

**Interfaces:**
- Consumes: `KnowledgeBaseV2` from Task 4.
- Produces: `KnowledgeV2Issue`; `validate_knowledge_v2(kb, *, allow_draft=False) -> tuple[KnowledgeV2Issue, ...]`.

```python
@dataclass(frozen=True)
class KnowledgeV2Issue:
    code: str
    object_id: str
    message_ru: str
```

- [ ] **Step 1: Write failing table-driven invariant tests**

```python
@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (unknown_action_target, "ACTION_TARGET_UNKNOWN"),
        (foreign_effect_action, "ACTION_EFFECT_MISMATCH"),
        (unknown_evidence_source, "EVIDENCE_SOURCE_UNKNOWN"),
        (indirect_only_supported_action, "DIRECT_EVIDENCE_REQUIRED"),
        (runtime_2026_scope, "VERSION_SCOPE_INVALID"),
        (wrong_host_profile, "HOST_PROFILE_INVALID"),
        (cyclic_template, "PROCEDURE_CYCLE"),
    ],
)
def test_validate_knowledge_v2_fails_closed(v2_kb, mutator, code) -> None:
    broken = mutator(v2_kb)
    assert code in {issue.code for issue in validate_knowledge_v2(broken)}
```

- [ ] **Step 2: Run validation tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_knowledge_v2_validation.py`

Expected: FAIL because the graph invariants are not implemented.

- [ ] **Step 3: Implement reference ownership checks**

Validate all of the following explicitly:

```text
capability.component_ref -> components
action.component_ref -> components
action.target_ref -> targets
action.effect_refs -> effects
effect.target_ref -> targets
evidence.source_id -> sources
evidence.supports_entity_refs -> known capability/action/effect/procedure
procedure.steps[*].action_ref -> actions
procedure.steps[*].evidence_ids -> evidence
procedure.dependencies -> steps in the same template
```

- [ ] **Step 4: Implement evidence and version gates**

Direct positive evidence is required for a positive capability/action/effect claim. Direct negative evidence is required for a negative claim. `project_policy` may validate workflow relations but cannot establish an upstream capability. Enforce:

```python
if target_release == "2026.1" and lifecycle_phase != LifecyclePhase.UPGRADE:
    issue("VERSION_SCOPE_INVALID", object_id, "2026.1 разрешён только для upgrade.")
if host_profile != "rocky_linux_9":
    issue("HOST_PROFILE_INVALID", object_id, "Ожидается Rocky Linux 9.")
```

- [ ] **Step 5: Run knowledge v2 test group**

Run: `.venv/bin/python -m pytest -q tests/test_knowledge_v2.py tests/test_knowledge_v2_validation.py tests/test_snapshot_trust.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/reqmap/knowledge_v2.py tests/test_knowledge_v2_validation.py tests/deep_factories.py
git commit -m "feat: validate deep knowledge graph invariants"
```

---

### Task 6: Canonical v2 manifest builder and signing tools

**Files:**
- Modify: `src/reqmap/snapshot_trust.py`
- Create: `tools/kb/build_snapshot_v2.py`
- Create: `tools/kb/sign_snapshot_v2.py`
- Create: `tests/test_snapshot_tools_v2.py`

**Interfaces:**
- Consumes: a structurally valid v2 snapshot and an external Ed25519 private key.
- Produces: `build_snapshot_manifest(root) -> dict[str, object]`; CLI tools that write `snapshot-manifest.json` and `snapshot-manifest.sig` atomically.

- [ ] **Step 1: Write failing deterministic-build and safe-signing tests**

```python
def test_build_manifest_is_deterministic_and_sorted(tmp_path: Path) -> None:
    root = unsigned_v2_snapshot(tmp_path, status="approved")
    first = build_snapshot_manifest(root)
    second = build_snapshot_manifest(root)
    assert canonical_manifest_bytes(first) == canonical_manifest_bytes(second)
    assert [item["path"] for item in first["files"]] == sorted(
        item["path"] for item in first["files"]
    )


def test_sign_tool_refuses_private_key_inside_snapshot(tmp_path: Path) -> None:
    root = unsigned_v2_snapshot(tmp_path, status="approved")
    key = root / "private-key"
    with pytest.raises(SnapshotTrustError, match="вне snapshot"):
        sign_snapshot_manifest(root, key)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_snapshot_tools_v2.py`

Expected: FAIL because the maintenance CLI tools do not exist.

- [ ] **Step 3: Wire the canonical builder to an atomic CLI tool**

Call `build_snapshot_manifest()` from Task 3. The CLI accepts exactly `--path SNAPSHOT`, then writes canonical JSON with `sort_keys=True`, compact separators, UTF-8 and final newline via `atomic_write_bytes()`. It refuses symlinks, sockets, devices, hidden temporary files and duplicate normalized paths before replacing an existing regular manifest.

- [ ] **Step 4: Implement signing with fixed namespace**

```python
subprocess.run(
    ["ssh-keygen", "-Y", "sign", "-f", str(private_key), "-n", "reqmap-snapshot", str(manifest_path)],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    timeout=10,
    check=False,
)
```

The signing CLI accepts exactly `--path SNAPSHOT --private-key KEY`. Move the generated `.sig` atomically to `snapshot-manifest.sig`. Never print the private key path or stderr. Refuse an unapproved snapshot, a key under snapshot root, a symlink key or an existing signature that is not a regular file.

- [ ] **Step 5: Run tool tests and round-trip verification**

Run: `.venv/bin/python -m pytest -q tests/test_snapshot_tools_v2.py tests/test_snapshot_trust.py`

Expected: PASS; a built and signed snapshot verifies, and one-byte tampering fails.

- [ ] **Step 6: Commit**

```bash
git add src/reqmap/snapshot_trust.py tools/kb/build_snapshot_v2.py tools/kb/sign_snapshot_v2.py tests/test_snapshot_tools_v2.py
git commit -m "feat: build and sign deep knowledge snapshots"
```

---

### Task 7: Safe v1 to draft v2 migration

**Files:**
- Create: `src/reqmap/migrate_v2.py`
- Create: `tests/test_migrate_v2.py`
- Modify: `tests/deep_factories.py`

**Interfaces:**
- Consumes: `KnowledgeBase` v1 from `load_knowledge()` and an empty destination directory.
- Produces: `MigrationError(ReqmapError)`, `migrate_v1_to_v2(source: Path, output: Path) -> MigrationReport` and an unsigned `snapshot_status=draft` tree.

```python
@dataclass(frozen=True)
class MigrationReport:
    source_snapshot_sha256: str
    output_path: Path
    components: int
    capabilities: int
    evidence: int
    inferred_actions: int
```

- [ ] **Step 1: Write failing lossless/draft/no-inference tests**

```python
def test_migration_preserves_v1_ids_and_creates_only_draft(tmp_path: Path) -> None:
    output = tmp_path / "v2"
    report = migrate_v1_to_v2(Path("tests/fixtures/kb_minimal"), output)
    kb = load_knowledge_v2_for_maintenance(output)

    assert kb.snapshot_status == "draft"
    assert set(kb.components) == {"nova", "kolla_ansible", "host_os_kernel_sysctl"}
    assert set(kb.capabilities) == {"CAP-NOVA", "CAP-KOLLA", "CAP-HOST-SYSCTL"}
    assert kb.actions == {}
    assert kb.effects == {}
    assert kb.procedures == {}
    assert all(item.review_state == "needs_review" for item in kb.evidence.values())
    assert not (output / "snapshot-manifest.sig").exists()
    assert report.inferred_actions == 0


def test_migration_refuses_nonempty_output(tmp_path: Path) -> None:
    output = tmp_path / "v2"
    output.mkdir()
    (output / "keep.txt").write_text("user data", encoding="utf-8")
    with pytest.raises(MigrationError, match="пуст"):
        migrate_v1_to_v2(Path("tests/fixtures/kb_minimal"), output)
    assert (output / "keep.txt").read_text(encoding="utf-8") == "user data"
```

- [ ] **Step 2: Run migration tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_migrate_v2.py`

Expected: FAIL because the migration module does not exist.

- [ ] **Step 3: Implement explicit contour derivation only from v1 component kind**

```python
KIND_TO_CONTOUR = {
    "openstack_service": "openstack_runtime",
    "deployment_tool": "kolla_ansible",
    "host_os_subsystem": "host_os",
}
```

Copy IDs, claims, locators, URLs, local files and hashes. Create actors/targets only when derivable from explicit component kind. Leave `actions.jsonl`, `effects.jsonl` and `procedures.jsonl` empty. Mark migrated evidence `review_state=needs_review`; set `snapshot_status=draft`; build `snapshot-manifest.json` with `build_snapshot_manifest()` but do not create `snapshot-manifest.sig`.

- [ ] **Step 4: Make writes recoverable and fail closed**

Build into a sibling temporary directory, validate through `load_knowledge_v2_for_maintenance()`, then rename to the absent destination. Refuse symlink/non-directory/non-empty destinations; delete only the tool-created temporary directory after a failed migration.

- [ ] **Step 5: Run migration and legacy knowledge regressions**

Run: `.venv/bin/python -m pytest -q tests/test_migrate_v2.py tests/test_knowledge.py tests/test_epoxy_snapshot.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/reqmap/migrate_v2.py tests/test_migrate_v2.py tests/deep_factories.py
git commit -m "feat: migrate legacy knowledge to draft v2"
```

---

### Task 8: Deterministic normalized retrieval and corpus discovery boundary

**Files:**
- Create: `src/reqmap/deep_retrieval.py`
- Create: `tests/test_deep_retrieval.py`
- Modify: `src/reqmap/deep_models.py`

**Interfaces:**
- Consumes: `KnowledgeBaseV2`, atom text, source hints, `top_k` and optional `CorpusCandidateProvider`.
- Produces: `CorpusCandidate`, `DeepCandidate`, `DeepRetrievalResult`; `retrieve_deep(kb, text, hints, top_k, corpus_provider=None)`.

```python
@dataclass(frozen=True)
class CorpusCandidate:
    source_id: str
    locator: str
    content_sha256: str
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DeepCandidate:
    component_ref: str
    capability_ref: str
    action_ref: str | None
    effect_ref: str | None
    evidence_ids: tuple[str, ...]
    version_scope: VersionScope
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DeepRetrievalResult:
    normalized_candidates: tuple[DeepCandidate, ...]
    corpus_candidates: tuple[CorpusCandidate, ...]
```

- [ ] **Step 1: Write failing deterministic and trust-boundary tests**

```python
def test_deep_retrieval_is_deterministic_and_version_filtered(v2_kb) -> None:
    first = retrieve_deep(v2_kb, "создать виртуальную машину через API", (), 8)
    second = retrieve_deep(v2_kb, "создать виртуальную машину через API", (), 8)
    assert to_dict(first) == to_dict(second)
    assert first.normalized_candidates[0].component_ref == "nova"
    assert all(item.version_scope.target_release == "2025.1" for item in first.normalized_candidates)


def test_corpus_candidate_never_carries_evidence_ids(v2_kb) -> None:
    result = retrieve_deep(v2_kb, "редкий термин", (), 8, FakeCorpusProvider())
    assert result.corpus_candidates
    assert not hasattr(result.corpus_candidates[0], "evidence_ids")
    assert result.normalized_candidates == ()
```

- [ ] **Step 2: Run retrieval tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_deep_retrieval.py`

Expected: FAIL because deep retrieval types/functions do not exist.

- [ ] **Step 3: Implement deterministic BM25 over normalized records**

Tokenize with NFKC, `casefold()`, `ё→е`, complete-token boundaries and existing synonyms. Build documents from capability name/terms, action identifiers, target identifiers and evidence claims. Use fixed BM25 constants `k1=1.2`, `b=0.75`, round final scores to 12 decimal places and tie-break by `(component_ref, capability_ref, action_ref or "")`.

```python
idf = math.log(1.0 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5))
score += idf * (frequency * (1.2 + 1.0)) / (
    frequency + 1.2 * (1.0 - 0.75 + 0.75 * document_length / average_length)
)
```

- [ ] **Step 4: Define corpus provider as discovery-only protocol**

```python
class CorpusCandidateProvider(Protocol):
    def retrieve(self, text: str, *, top_k: int) -> tuple[CorpusCandidate, ...]:
        raise NotImplementedError


class NullCorpusCandidateProvider:
    def retrieve(self, text: str, *, top_k: int) -> tuple[CorpusCandidate, ...]:
        return ()
```

Reject corpus candidates with unknown `source_id`, unsafe locator, non-finite score or content hash mismatch. Do not convert them into `DeepEvidence`.

- [ ] **Step 5: Run deep and legacy retrieval tests**

Run: `.venv/bin/python -m pytest -q tests/test_deep_retrieval.py tests/test_retrieval.py`

Expected: PASS and legacy ordering unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/reqmap/deep_retrieval.py src/reqmap/deep_models.py tests/test_deep_retrieval.py
git commit -m "feat: retrieve deep evidence candidates offline"
```

---

### Task 9: Deep responsibility mapping and evidence gate

**Files:**
- Create: `src/reqmap/deep_mapping.py`
- Modify: `src/reqmap/prompts.py`
- Create: `tests/test_deep_mapping.py`
- Modify: `tests/deep_factories.py`

**Interfaces:**
- Consumes: `JsonModel`, `AtomicClaim`, `DeepRetrievalResult`, `KnowledgeBaseV2`.
- Produces: `DeepMappingOutcome`; `map_atom_deep(model, atom, retrieval, kb) -> DeepMappingOutcome`; `validate_responsibility_records()`.

```python
@dataclass(frozen=True)
class DeepMappingOutcome:
    atom_result: DeepAtomResult
    responsibility_records: tuple[ResponsibilityRecord, ...]
    procedure_template_ids: tuple[str, ...]
```

- [ ] **Step 1: Write failing supported, mixed, conflict and fail-closed tests**

```python
def test_mixed_host_change_creates_kolla_and_host_records(v2_kb) -> None:
    outcome = map_atom_deep(
        FakeModel([mixed_sysctl_response()]),
        atom("Применить net.ipv4.ip_forward через Kolla-Ansible"),
        sysctl_retrieval(v2_kb),
        v2_kb,
    )
    assert [item.contour for item in outcome.responsibility_records] == [
        ResponsibilityContour.KOLLA_ANSIBLE,
        ResponsibilityContour.HOST_OS,
    ]
    host = outcome.responsibility_records[1]
    assert host.executor_ref == "actor:kolla_ansible"
    assert host.target_ref == "rocky_linux_9.kernel_sysctl"
    assert outcome.responsibility_records[0].related_record_ids == (host.record_id,)


def test_kolla_only_host_response_is_rejected(v2_kb) -> None:
    outcome = map_atom_deep(
        FakeModel([kolla_only_sysctl_response(), kolla_only_sysctl_response()]),
        atom("Применить sysctl"),
        sysctl_retrieval(v2_kb),
        v2_kb,
    )
    assert outcome.atom_result.analysis_state is AnalysisState.VALIDATION_FAILED
    assert "HOST_OS_RECORD_REQUIRED" in outcome.atom_result.diagnostics[0]


def test_positive_and_negative_direct_evidence_produces_conflict(v2_kb) -> None:
    outcome = map_atom_deep(
        FakeModel([conflicting_response()]), atom(), conflicting_retrieval(), v2_kb
    )
    assert outcome.atom_result.analysis_state is AnalysisState.COMPLETED
    assert outcome.atom_result.support_status is SupportStatus.INSUFFICIENT_EVIDENCE
    assert any("evidence_conflict" in item for item in outcome.atom_result.diagnostics)
```

- [ ] **Step 2: Run mapping tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_deep_mapping.py`

Expected: FAIL because deep mapping and prompt do not exist.

- [ ] **Step 3: Add versioned strict response contract**

Add `PROMPT_DEEP_MAPPING_VERSION = "2.0"` and `DEEP_MAPPING_PROMPT`. The response must contain only:

```json
{
  "support_status": "supported",
  "supported_aspects": ["Создание виртуальной машины"],
  "unconfirmed_aspects": [],
  "responsibilities": [
    {
      "contour": "openstack_runtime",
      "component_ref": "nova",
      "executor_ref": "actor:nova_api",
      "target_contour": "openstack_runtime",
      "target_ref": "nova.server",
      "action_ref": "ACTION-NOVA-SERVER-CREATE",
      "effect_ref": "EFFECT-NOVA-SERVER-EXISTS",
      "lifecycle_phase": "runtime",
      "version_scope": {
        "source_release": "2025.1",
        "target_release": "2025.1",
        "kolla_ansible_release": "2025.1",
        "host_profile": "rocky_linux_9",
        "version_constraint": "2025.1"
      },
      "evidence_ids": ["E-NOVA-CREATE"],
      "support_status": "supported",
      "related_indexes": []
    }
  ],
  "procedure_template_ids": []
}
```

The model never supplies IDs, commands outside `ActionRecord`, step ordering or arbitrary source excerpts. Retry exactly once with validation feedback, matching v1 retry behavior.

- [ ] **Step 4: Build canonical records and resolve relations locally**

Assign IDs with `responsibility_id(atom.atom_id, ordinal)`. Convert 1-based `related_indexes` to stable IDs after validating range, uniqueness, symmetry and absence of self-links. Candidate component/capability/action/effect/evidence sets are hard allowlists.

- [ ] **Step 5: Implement evidence and cross-contour gate**

Enforce these exact results:

```text
direct positive and no applicable direct negative -> supported allowed
direct negative and no applicable direct positive -> not_supported allowed
positive + negative direct -> insufficient_evidence + evidence_conflict
no direct evidence -> insufficient_evidence
unknown/indirect-only evidence with claimed supported -> validation failure
Kolla executor targeting host OS -> linked kolla_ansible and host_os records required
target_release 2026.1 outside upgrade -> validation failure
```

Corpus candidates never enter `evidence_ids`.

- [ ] **Step 6: Run deep mapping plus v1 mapping regression**

Run: `.venv/bin/python -m pytest -q tests/test_deep_mapping.py tests/test_mapping.py`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/reqmap/deep_mapping.py src/reqmap/prompts.py tests/test_deep_mapping.py tests/deep_factories.py
git commit -m "feat: map atoms to verified responsibility contours"
```

---

### Task 10: Procedure template instantiation and DAG validation

**Files:**
- Create: `src/reqmap/procedure.py`
- Create: `tests/test_procedure.py`
- Modify: `src/reqmap/knowledge_v2.py`
- Modify: `tests/deep_factories.py`

**Interfaces:**
- Consumes: selected `procedure_template_ids`, responsibility records and `KnowledgeBaseV2`.
- Produces: `ProcedureError(ReqmapError)`, `ProcedureIssue`, `ProcedureBuildResult`; `instantiate_procedure_graphs(requirement_id, template_ids, responsibilities, kb)` and `validate_procedure_graph(graph, responsibilities, kb)`.

```python
@dataclass(frozen=True)
class ProcedureIssue:
    code: str
    object_id: str
    message_ru: str


@dataclass(frozen=True)
class ProcedureBuildResult:
    graphs: tuple[ProcedureGraph, ...]
    responsibility_records: tuple[ResponsibilityRecord, ...]
    diagnostics: tuple[str, ...]
```

- [ ] **Step 1: Write failing DAG, evidence and gap tests**

```python
def test_instantiated_procedure_has_preflight_action_verify_and_rollback(v2_kb) -> None:
    result = instantiate_procedure_graphs(
        "REQ-0001", ("PROC-SYSCTL",), mixed_sysctl_records(), v2_kb
    )
    graph = result.graphs[0]
    assert [step.phase for step in graph.steps] == [
        LifecyclePhase.PREFLIGHT,
        LifecyclePhase.RECONFIGURE,
        LifecyclePhase.VERIFY,
        LifecyclePhase.ROLLBACK,
    ]
    assert validate_procedure_graph(graph, mixed_sysctl_records(), v2_kb) == ()


def test_required_procedure_without_template_is_explicit_gap(v2_kb) -> None:
    result = instantiate_procedure_graphs(
        "REQ-0001", (), migration_records(), v2_kb
    )
    assert result.graphs == ()
    assert result.diagnostics == ("procedure_gap:ACTION-MIGRATE-SERVER",)


def test_cycle_is_rejected(v2_kb) -> None:
    with pytest.raises(ProcedureError, match="PROCEDURE_CYCLE"):
        instantiate_procedure_graphs(
            "REQ-0001", ("PROC-CYCLIC",), records(), v2_kb
        )
```

- [ ] **Step 2: Run procedure tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_procedure.py`

Expected: FAIL because procedure instantiation does not exist.

- [ ] **Step 3: Finalize template record contract**

Each template step stores `local_step_id`, phase, contour, actor/target/action refs, preconditions, success criteria, evidence IDs, `depends_on` and optional `rollback_step_local_id`. `ActionRecord.procedure_required` is `True` for deploy, reconfigure, upgrade, migrate and recover actions that cannot be represented safely as a single runtime API operation.

- [ ] **Step 4: Instantiate stable graph and step IDs**

```python
graph_id = procedure_graph_id(requirement_id, graph_ordinal)
step_ids = {
    template_step.local_step_id: procedure_step_id(graph_id, ordinal)
    for ordinal, template_step in enumerate(template.steps, start=1)
}
```

Translate dependencies and rollback references through `step_ids`; attach resulting step IDs to related `ResponsibilityRecord` copies. Preserve topological order with stable source-order tie breaks.

- [ ] **Step 5: Validate graph completeness**

Reject cycles, missing dependencies, cross-graph references, unknown actors/targets/actions/evidence, contour mismatch, blank preconditions/success criteria and action steps without direct evidence. Missing rollback evidence yields `rollback_unverified`; it does not generate a synthetic rollback command.

- [ ] **Step 6: Run procedure and knowledge v2 tests**

Run: `.venv/bin/python -m pytest -q tests/test_procedure.py tests/test_knowledge_v2_validation.py`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/reqmap/procedure.py src/reqmap/knowledge_v2.py tests/test_procedure.py tests/deep_factories.py
git commit -m "feat: build evidence-backed procedure graphs"
```

---

### Task 11: Deep aggregation and canonical graph validation

**Files:**
- Create: `src/reqmap/deep_aggregation.py`
- Create: `tests/test_deep_aggregation.py`
- Modify: `src/reqmap/deep_models.py`

**Interfaces:**
- Consumes: per-atom mapping outcomes, procedure build results and source requirements.
- Produces: `aggregate_deep_requirement()`, `aggregate_deep_groups()`, `deep_run_status()` and `validate_deep_graph()`.

- [ ] **Step 1: Write failing status and traceability tests**

```python
def test_deep_run_is_partial_when_completed_requirement_has_procedure_gap() -> None:
    result = completed_deep_requirement(diagnostics=("procedure_gap:ACTION-MIGRATE",))
    assert deep_run_status((result,), preflight_ok=True) == "PARTIAL"


def test_deep_graph_requires_all_responsibility_links() -> None:
    run = deep_run()
    forged = replace(run, responsibility_records=run.responsibility_records[:-1])
    with pytest.raises(ValueError, match="responsibility"):
        validate_deep_graph(forged)


def test_mixed_requirement_preserves_all_contours() -> None:
    result, records = aggregate_deep_requirement(
        requirement(), mixed_atom_outcomes()
    )
    assert {record.contour for record in records} == {
        ResponsibilityContour.KOLLA_ANSIBLE,
        ResponsibilityContour.HOST_OS,
    }
    assert result.responsibility_ids == tuple(record.record_id for record in records)
```

- [ ] **Step 2: Run aggregation tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_deep_aggregation.py`

Expected: FAIL because deep aggregation does not exist.

- [ ] **Step 3: Implement support and operational aggregation separately**

Reuse v1 support precedence for completed atoms. Compute operational status separately:

```python
PARTIAL_DIAGNOSTIC_PREFIXES = (
    "evidence_conflict",
    "responsibility_ambiguous",
    "procedure_gap",
    "rollback_unverified",
)

def deep_run_status(results, preflight_ok):
    if not preflight_ok or not results:
        return "FAILED"
    completed = sum(item.analysis_state is AnalysisState.COMPLETED for item in results)
    if completed != len(results):
        return "PARTIAL" if completed else "FAILED"
    if any(
        diagnostic.startswith(PARTIAL_DIAGNOSTIC_PREFIXES)
        for item in results
        for diagnostic in item.diagnostics
    ):
        return "PARTIAL"
    return "SUCCESS"
```

- [ ] **Step 4: Implement full graph validation**

Validate stable and unique IDs; requirement/atom/source quote traceability; flattened responsibility lists; symmetric related links; responsibility-to-step links; graph-to-step containment; cited evidence equality; group recomputation; version scopes; no orphan actions/effects/procedures.

- [ ] **Step 5: Run deep and legacy aggregation tests**

Run: `.venv/bin/python -m pytest -q tests/test_deep_aggregation.py tests/test_aggregation.py tests/test_models.py`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/reqmap/deep_aggregation.py src/reqmap/deep_models.py tests/test_deep_aggregation.py
git commit -m "feat: aggregate and validate deep result graphs"
```

---

### Task 12: Canonical deep JSON and deep run manifest

**Files:**
- Create: `src/reqmap/export_deep_json.py`
- Modify: `src/reqmap/manifest.py`
- Create: `tests/test_export_deep_json.py`
- Modify: `tests/test_json_manifest.py`

**Interfaces:**
- Consumes: validated `DeepRunResult`.
- Produces: `write_deep_canonical_json()`, `validate_deep_run_result()`, `write_deep_manifest()` and `write_deep_preflight_artifacts()`.

- [ ] **Step 1: Write failing canonical JSON and allowlisted manifest tests**

```python
def test_deep_json_is_deterministic_and_contains_normalized_graph(tmp_path: Path) -> None:
    run = deep_run()
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    assert write_deep_canonical_json(run, first) == write_deep_canonical_json(run, second)
    assert first.read_bytes() == second.read_bytes()
    payload = json.loads(first.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "2.0"
    assert payload["responsibility_records"][0]["atomic_claim_id"] == "REQ-0001-A001"
    assert payload["procedure_graphs"][0]["steps"][0]["evidence_ids"]


def test_deep_manifest_contains_trust_metadata_without_paths_or_secrets(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    write_deep_manifest(deep_run(), {"result.json": "a" * 64}, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["knowledge_trust"] == {
        "key_id": "reqmap-maintenance-2026",
        "manifest_sha256": "b" * 64,
        "signer_identity": "reqmap-snapshot",
    }
    assert "allowed_signers_path" not in path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run JSON/manifest tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_export_deep_json.py tests/test_json_manifest.py`

Expected: FAIL because deep writers do not exist.

- [ ] **Step 3: Implement deep JSON through existing atomic primitives**

Call `validate_deep_graph()` before serializing. Reuse `canonical_json_bytes()` and `atomic_write_bytes()` from `export_json.py`; do not duplicate symlink, permissions or fsync logic.

- [ ] **Step 4: Add a separate allowlisted deep manifest writer**

Include only schema/run IDs, run status, reqmap/model safe metadata, input hash, snapshot ID, manifest hash, key ID, signer identity, prompt versions, release profile, retry counts and artifact hashes. Exclude API keys, complete model URL, trusted signer path, source URLs and private-key information.

- [ ] **Step 5: Implement failed-preflight artifacts**

`write_deep_preflight_artifacts()` writes only `run.jsonl` and `manifest.json`; requirements are `skipped`, support statuses are null, and no `result.json`, XLSX or report is created.

- [ ] **Step 6: Run JSON, manifest and legacy writer regressions**

Run: `.venv/bin/python -m pytest -q tests/test_export_deep_json.py tests/test_json_manifest.py`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/reqmap/export_deep_json.py src/reqmap/manifest.py tests/test_export_deep_json.py tests/test_json_manifest.py
git commit -m "feat: export canonical deep JSON and manifests"
```

---

### Task 13: Deep preflight, pipeline and checkpoint resume

**Files:**
- Create: `src/reqmap/output_safety.py`
- Create: `src/reqmap/deep_pipeline.py`
- Modify: `src/reqmap/pipeline.py`
- Create: `tests/test_deep_pipeline.py`
- Modify: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `AppConfig` deep profile, `AnalysisRequest`, `JsonModel`, signed `KnowledgeBaseV2`.
- Produces: `preflight_deep(config, model)`, `analyze_deep(request, config, model) -> DeepRunResult`; shared safe output helpers used by both pipelines.

- [ ] **Step 1: Write failing preflight, orchestration and resume tests**

```python
def test_deep_preflight_verifies_snapshot_before_model(tmp_path: Path) -> None:
    config = deep_config(tampered_snapshot(tmp_path))
    model = FakeModel()
    result = preflight_deep(config, model)
    assert result.ok is False
    assert result.diagnostics[0].startswith("SNAPSHOT_")
    assert model.preflight_calls == 0


def test_deep_pipeline_processes_mixed_requirement(tmp_path: Path) -> None:
    run = analyze_deep(
        request_for(tmp_path, "Применить sysctl через Kolla-Ansible"),
        signed_deep_config(tmp_path),
        FakeModel([decomposition_response(), mixed_sysctl_response()]),
    )
    assert run.run_status == "SUCCESS"
    assert {item.contour for item in run.responsibility_records} == {
        ResponsibilityContour.KOLLA_ANSIBLE,
        ResponsibilityContour.HOST_OS,
    }


def test_deep_resume_signature_includes_manifest_and_profile(tmp_path: Path) -> None:
    first = analyze_deep(
        request_for(tmp_path), signed_deep_config(tmp_path), populated_model()
    )
    resumed_model = FakeModel()
    second = analyze_deep(
        request_for(tmp_path), signed_deep_config(tmp_path), resumed_model
    )
    assert first.requirements == second.requirements
    assert resumed_model.subject_calls == []
```

- [ ] **Step 2: Run pipeline tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_deep_pipeline.py tests/test_pipeline.py`

Expected: FAIL because deep pipeline does not exist.

- [ ] **Step 3: Extract output safety without behavioral changes**

Move the existing request/output checks and stale-artifact cleanup from `pipeline.py` into:

```python
request_diagnostics = validate_analysis_request(request)
if request_diagnostics:
    return PreflightResult(False, request_diagnostics, None)

cleanup_diagnostics = clear_published_artifacts(request.output_dir)
if cleanup_diagnostics:
    return PreflightResult(False, cleanup_diagnostics, None)
```

Rename the current `_request_diagnostics()` body to public `validate_analysis_request()` and the current `_clear_published_artifacts()` body to public `clear_published_artifacts()` without changing branches or diagnostic strings. Update legacy imports and run all current symlink/collision/cleanup tests before adding deep orchestration.

- [ ] **Step 4: Implement ordered deep preflight**

Order is fixed:

```text
canonical config/profile checks
→ input/output safety
→ signature and snapshot integrity
→ v2 schema/references/version validation
→ model preflight
```

No model call occurs after any knowledge/trust failure. Deep profile with v1 snapshot fails `KNOWLEDGE_SCHEMA_UNSUPPORTED`.

- [ ] **Step 5: Implement deep requirement loop**

For each requirement: reuse `decompose()`, then `retrieve_deep()`, `map_atom_deep()`, `instantiate_procedure_graphs()` and `aggregate_deep_requirement()`. Isolate model/validation failures per requirement exactly as legacy does.

- [ ] **Step 6: Implement profile-bound checkpoint signature and strict decode**

Hash input, snapshot manifest SHA-256, snapshot ID, key ID, model, seed, top_k, analysis profile and prompt versions. Checkpoint schema is `2.0`. On decode, re-run `validate_deep_graph()` for the single requirement and reject unknown references, changed signature, non-completed state or non-canonical IDs.

- [ ] **Step 7: Run focused and legacy pipeline tests**

Run: `.venv/bin/python -m pytest -q tests/test_deep_pipeline.py tests/test_pipeline.py tests/test_cli_analyze.py`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/reqmap/output_safety.py src/reqmap/deep_pipeline.py src/reqmap/pipeline.py tests/test_deep_pipeline.py tests/test_pipeline.py
git commit -m "feat: orchestrate deep analysis with verified resume"
```

---

### Task 14: Deep XLSX, Markdown and cross-artifact verification

**Files:**
- Create: `src/reqmap/export_deep_xlsx.py`
- Create: `src/reqmap/export_deep_markdown.py`
- Create: `src/reqmap/crosscheck_deep.py`
- Create: `tests/test_export_deep_xlsx.py`
- Create: `tests/test_export_deep_markdown.py`
- Create: `tests/test_crosscheck_deep.py`

**Interfaces:**
- Consumes: canonical `DeepRunResult`.
- Produces: `write_deep_xlsx()`, `write_deep_markdown()`, `crosscheck_deep()`.

- [ ] **Step 1: Write failing workbook/report structure tests**

```python
def test_deep_workbook_has_exact_sheets_and_counts(tmp_path: Path) -> None:
    path = tmp_path / "result.xlsx"
    write_deep_xlsx(deep_run(), path)
    workbook = load_workbook(path, read_only=True, data_only=False)
    try:
        assert workbook.sheetnames == [
            "Требования",
            "Атомарные утверждения",
            "Ответственность",
            "Процедуры",
            "Доказательства",
            "Диагностика",
            "Запуск",
        ]
        assert workbook["Ответственность"].max_row - 1 == 2
        assert workbook["Процедуры"].max_row - 1 == 4
    finally:
        workbook.close()


def test_deep_markdown_exposes_contours_gaps_and_trust(tmp_path: Path) -> None:
    path = tmp_path / "report.md"
    write_deep_markdown(deep_partial_run(), path)
    text = path.read_text(encoding="utf-8")
    assert "## Контуры ответственности" in text
    assert "procedure_gap" in text
    assert "reqmap-maintenance-2026" in text
    assert "allowed_signers" not in text
```

- [ ] **Step 2: Run exporter tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_export_deep_xlsx.py tests/test_export_deep_markdown.py tests/test_crosscheck_deep.py`

Expected: FAIL because deep exporters do not exist.

- [ ] **Step 3: Implement exact deep workbook contract**

Use existing deterministic ZIP normalization and atomic write helpers. Add one row per source requirement, atom, responsibility, procedure step, cited evidence and diagnostic. Serialize tuples as canonical JSON in cells; never concatenate IDs ambiguously. `Запуск` includes schema/profile/releases/snapshot/key/manifest/model-safe metadata.

- [ ] **Step 4: Implement Russian Markdown with embedded canonical count marker**

Marker keys are:

```json
{"requirements":0,"atoms":0,"responsibilities":0,"procedure_steps":0,"evidence":0,"diagnostics":0}
```

Render summary, per-contour tables, executor→target links, version scope, procedure DAGs, evidence conflicts, gaps/rollback warnings and processing failures. Preserve full original requirement text in every problematic entry.

- [ ] **Step 5: Implement independent deep crosscheck**

Parse `result.json`, workbook in read-only mode and Markdown marker. Compare all IDs, counts, contours, relationships, statuses, version scopes, diagnostics and evidence references cell by cell against canonical JSON. Reject formulas, hidden sheets, unknown sheets or duplicate IDs.

- [ ] **Step 6: Run deep exporters and legacy exporter regressions**

Run: `.venv/bin/python -m pytest -q tests/test_export_deep_xlsx.py tests/test_export_deep_markdown.py tests/test_crosscheck_deep.py tests/test_export_xlsx.py tests/test_export_markdown.py tests/test_crosscheck.py`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/reqmap/export_deep_xlsx.py src/reqmap/export_deep_markdown.py src/reqmap/crosscheck_deep.py tests/test_export_deep_xlsx.py tests/test_export_deep_markdown.py tests/test_crosscheck_deep.py
git commit -m "feat: export and crosscheck deep analysis artifacts"
```

---

### Task 15: CLI dispatch and knowledge maintenance commands

**Files:**
- Modify: `src/reqmap/cli.py`
- Create: `tests/test_cli_deep.py`
- Modify: `tests/test_cli_analyze.py`
- Modify: `tests/test_cli_version.py`

**Interfaces:**
- Consumes: `AppConfig.analysis_profile`, legacy/deep pipelines and exporters, v2 loader, migration function.
- Produces: profile dispatch; `knowledge validate` v1/v2; `knowledge migrate-v1`.

- [ ] **Step 1: Write failing dispatch and maintenance CLI tests**

```python
def test_cli_dispatches_deep_profile_and_publishes_five_artifacts(
    tmp_path: Path, capsys
) -> None:
    exit_code = main(deep_cli_arguments(tmp_path))
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Профиль анализа: deep" in captured.out
    assert "Статус запуска: SUCCESS" in captured.out
    assert sorted(
        path.name for path in deep_output(tmp_path).iterdir() if path.is_file()
    ) == ["manifest.json", "report.md", "result.json", "result.xlsx", "run.jsonl"]


def test_knowledge_validate_v2_requires_allowed_signers(tmp_path: Path, capsys) -> None:
    root, _ = signed_v2_snapshot(tmp_path)
    assert main(["knowledge", "validate", "--path", str(root)]) == 2
    assert "--allowed-signers" in capsys.readouterr().err


def test_knowledge_migrate_v1_creates_draft(tmp_path: Path) -> None:
    output = tmp_path / "draft"
    assert main([
        "knowledge", "migrate-v1",
        "--source", "tests/fixtures/kb_minimal",
        "--output", str(output),
    ]) == 0
    assert json.loads((output / "metadata.json").read_text())["snapshot_status"] == "draft"
```

- [ ] **Step 2: Run CLI tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_cli_deep.py tests/test_cli_analyze.py tests/test_cli_version.py`

Expected: FAIL because CLI has no deep dispatch or migration subcommand.

- [ ] **Step 3: Dispatch analysis without weakening type checks**

```python
if config.analysis_profile is AnalysisProfile.DEEP:
    run = analyze_deep(request, config, model)
    publisher = _publish_deep_artifacts
else:
    run = analyze(request, config, model)
    publisher = _publish_artifacts
```

Keep existing exit codes `0/2/3/4/5/6`. Deep `PARTIAL` returns `4`; deep failed preflight creates only diagnostics and returns `3`.

- [ ] **Step 4: Extend knowledge parser safely**

`knowledge validate` auto-detects schema from strict `metadata.json`; schema v1 uses current loader, schema v2 requires `--allowed-signers PATH`. Add `knowledge migrate-v1 --source PATH --output PATH --debug`. Do not add a public signing command that accepts a private key through reqmap runtime CLI; signing remains an online maintenance tool.

- [ ] **Step 5: Publish deep artifacts and summaries**

Use deep writers and `crosscheck_deep`; write run manifest after closing `run.jsonl`. Print profile, run status, incomplete requirement IDs, gap/conflict counts and absolute artifact paths with SHA-256. Do not call legacy exporters with a `DeepRunResult`.

- [ ] **Step 6: Run CLI and legacy acceptance-focused tests**

Run: `.venv/bin/python -m pytest -q tests/test_cli_deep.py tests/test_cli_analyze.py tests/test_cli_version.py tests/test_skill_contract.py`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/reqmap/cli.py tests/test_cli_deep.py tests/test_cli_analyze.py tests/test_cli_version.py
git commit -m "feat: expose deep analysis through reqmap CLI"
```

---

### Task 16: Frozen gold set, end-to-end acceptance, offline contract and docs

**Files:**
- Create: `tests/fixtures/deep_gold.json`
- Create: `tests/test_deep_gold.py`
- Create: `tests/test_deep_acceptance.py`
- Modify: `tests/test_acceptance.py`
- Modify: `tests/test_distribution.py`
- Modify: `tests/test_docs_language.py`
- Modify: `README.md`
- Modify: `INSTALL_OFFLINE.md`
- Modify: `RUNBOOK.md`
- Modify: `KNOWLEDGE_BASE.md`
- Modify: `OUTPUT_SCHEMA.md`
- Modify: `TROUBLESHOOTING.md`
- Modify: `CLIENTS_CODEX_OPENCODE.md`
- Modify: `.agents/skills/reqmap/SKILL.md`

**Interfaces:**
- Consumes: complete deep foundation from Tasks 1–15.
- Produces: frozen contract cases, signed-snapshot end-to-end proof, operator documentation and final regression evidence.

- [ ] **Step 1: Add a frozen gold set with explicit expected records**

`deep_gold.json` contains at least these IDs:

```text
GOLD-OPENSTACK-RUNTIME
GOLD-KOLLA-RECONFIGURE
GOLD-HOST-OS
GOLD-MIXED-KOLLA-HOST
GOLD-HA-PROCEDURE-GAP
GOLD-MIGRATE-VM
GOLD-MIGRATE-VOLUME
GOLD-MIGRATE-NETWORK
GOLD-MIGRATE-IMAGE
GOLD-MIGRATE-CONTROL-PLANE
GOLD-MIGRATE-DATABASE
GOLD-UPGRADE-2025-TO-2026
GOLD-AMBIGUOUS
GOLD-INSUFFICIENT
GOLD-CONFLICT
GOLD-ADVERSARIAL-FALSE-SUPPORT
```

Each object stores the literal requirement, expected atom quotes, expected contours, executor/target pairs, version scope, expected support/analysis states, mandatory diagnostic prefixes and allowed procedure template IDs.

- [ ] **Step 2: Write failing gold evaluator and E2E tests**

```python
def test_gold_set_has_zero_false_supported(deep_gold_results) -> None:
    false_supported = [
        case["id"]
        for case, result in deep_gold_results
        if case["expected_support"] != "supported"
        and result.support_status is SupportStatus.SUPPORTED
    ]
    assert false_supported == []


def test_deep_acceptance_is_traceable_cross_artifact_and_offline(
    tmp_path: Path, fake_openai_server
) -> None:
    root, allowed_signers = signed_v2_snapshot(tmp_path)
    exit_code = main(
        deep_gold_cli_args(root, allowed_signers, fake_openai_server, tmp_path)
    )
    assert exit_code == 4
    result = json.loads((tmp_path / "out/result.json").read_text(encoding="utf-8"))
    assert len(source_quotes(result)) == len(expected_source_quotes())
    assert external_requests(fake_openai_server) == []
    assert crosscheck_deep_paths(tmp_path / "out") == ()
```

- [ ] **Step 3: Run gold/acceptance tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_deep_gold.py tests/test_deep_acceptance.py`

Expected: FAIL until fixtures, fake responses and documentation contracts are complete.

- [ ] **Step 4: Complete test fixtures without weakening expected outcomes**

Generate Ed25519 keys only in `tmp_path`; use a local fake OpenAI-compatible server; assert the only network requests are `GET /v1/models` and `POST /v1/chat/completions` to that server. Do not monkeypatch evidence gate results. Verify all mixed cases, procedure gaps, rollback warnings and version failures through public CLI artifacts.

- [ ] **Step 5: Update operator documentation with exact boundaries**

Document:

```text
legacy remains default
deep requires schema v2 + approved signed snapshot + external trusted signer file
ssh-keygen with -Y support is a deep runtime prerequisite
runtime never opens source URLs
production deep corpus is not delivered by this architectural stage
v1 migration produces unsigned draft only
2026.1 applies only to upgrade
Rocky Linux 9 is the only host profile
PARTIAL lists evidence_conflict/responsibility_ambiguous/procedure_gap/rollback_unverified
```

Add ready commands for validate, analyze, migrate, build manifest and sign manifest. Keep private keys and API keys environment-local and outside the repository/snapshot.

- [ ] **Step 6: Run targeted deep and documentation suites**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_deep_models.py \
  tests/test_snapshot_trust.py \
  tests/test_knowledge_v2.py \
  tests/test_knowledge_v2_validation.py \
  tests/test_migrate_v2.py \
  tests/test_deep_retrieval.py \
  tests/test_deep_mapping.py \
  tests/test_procedure.py \
  tests/test_deep_aggregation.py \
  tests/test_export_deep_json.py \
  tests/test_deep_pipeline.py \
  tests/test_export_deep_xlsx.py \
  tests/test_export_deep_markdown.py \
  tests/test_crosscheck_deep.py \
  tests/test_cli_deep.py \
  tests/test_deep_gold.py \
  tests/test_deep_acceptance.py \
  tests/test_distribution.py \
  tests/test_docs_language.py \
  tests/test_skill_contract.py
```

Expected: PASS with zero skips except an explicitly platform-marked OpenSSH probe when `ssh-keygen -Y` is unavailable; the supported Rocky Linux 9 acceptance environment must not skip it.

- [ ] **Step 7: Run full regression and static checks**

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src tools tests
git diff --check
rg -n 'TO[D]O|TB[D]|FIX[M]E|PLACE[H]OLDER' \
  src tests tools .agents README.md INSTALL_OFFLINE.md RUNBOOK.md \
  KNOWLEDGE_BASE.md OUTPUT_SCHEMA.md CLIENTS_CODEX_OPENCODE.md TROUBLESHOOTING.md
```

Expected: all tests PASS, `compileall` exit `0`, `git diff --check` empty and placeholder scan empty.

- [ ] **Step 8: Run clean offline distribution acceptance**

Create a clean source archive excluding `.git`, `.venv`, results and private keys. In an isolated Linux environment with external network disabled:

```bash
./install.sh .deep-venv
.deep-venv/bin/python -m pytest -q
REQMAP_ACCEPTANCE_DIR="$(mktemp -d)"
cp -R tests/fixtures/kb_v2_minimal "$REQMAP_ACCEPTANCE_DIR/snapshot"
mkdir -p "$REQMAP_ACCEPTANCE_DIR/trust"
ssh-keygen -q -t ed25519 -N '' -f "$REQMAP_ACCEPTANCE_DIR/trust/signing_key"
awk '{print "reqmap-snapshot " $1 " " $2}' \
  "$REQMAP_ACCEPTANCE_DIR/trust/signing_key.pub" \
  > "$REQMAP_ACCEPTANCE_DIR/trust/allowed_signers"
.deep-venv/bin/python tools/kb/build_snapshot_v2.py \
  --path "$REQMAP_ACCEPTANCE_DIR/snapshot"
.deep-venv/bin/python tools/kb/sign_snapshot_v2.py \
  --path "$REQMAP_ACCEPTANCE_DIR/snapshot" \
  --private-key "$REQMAP_ACCEPTANCE_DIR/trust/signing_key"
.deep-venv/bin/reqmap knowledge validate \
  --path "$REQMAP_ACCEPTANCE_DIR/snapshot" \
  --allowed-signers "$REQMAP_ACCEPTANCE_DIR/trust/allowed_signers"
```

Expected: installation uses only `vendor/wheels`; full suite passes; signed v2 validation succeeds; source URLs are never contacted. Record exact OS, architecture, Python, OpenSSH, test count and snapshot manifest hash in the acceptance report section of `README.md`.

- [ ] **Step 9: Commit final contracts and documentation**

```bash
git add tests/fixtures/deep_gold.json tests/test_deep_gold.py tests/test_deep_acceptance.py tests/test_acceptance.py tests/test_distribution.py tests/test_docs_language.py README.md INSTALL_OFFLINE.md RUNBOOK.md KNOWLEDGE_BASE.md OUTPUT_SCHEMA.md TROUBLESHOOTING.md CLIENTS_CODEX_OPENCODE.md .agents/skills/reqmap/SKILL.md
git commit -m "test: verify deep evidence contour contract"
```

---

## Final Verification Gate

After Task 16, run these commands again from a clean worktree and report exact outputs rather than inferred success:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src tools tests
.venv/bin/reqmap --version
.venv/bin/reqmap knowledge validate --path knowledge/epoxy-2025.1
git diff --check
git status --short
```

Then perform the deep signed-snapshot validation from the isolated Linux acceptance environment. Do not claim production deep coverage: this plan proves the architecture and contracts only; OpenStack/Kolla/Rocky corpus population remains the next project stage.
