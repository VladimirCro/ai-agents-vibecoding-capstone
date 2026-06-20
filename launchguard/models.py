"""
launchguard.models — Shared data shapes for the LaunchGuard three-source reconciliation pipeline.

All types are stdlib dataclasses (pydantic is not installed in the current environment).
Field names match api-contracts.yaml EXACTLY — deviations are noted in comments.
Provides to_dict() / from_dict() for ADK session-state serialization (session state
uses plain dicts / JSON).

This module is stdlib-only and always importable — zero external dependencies.

Shapes defined here:
  Tool fragments   : DockerfileFacts, EntrypointFacts, CodeMatch, Evidence
  Core data shapes : IntendedContract, DeclaredState, LiveState, LiveStateFragment
                     ReconciliationDelta, ProposedPatch, ReadinessScorecard
  Enumerations     : DeltaClass, RuleId, Verdict, PortSource, HostBinding, PatchKind, Mode
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Enumerations (string literals — match api-contracts.yaml enum values exactly)
# ---------------------------------------------------------------------------

class DeltaClass:
    """ReconciliationDelta.delta_class values (api-contracts.yaml)."""
    WILL_FAIL: str = "will-fail"
    WILL_MISBEHAVE: str = "will-misbehave"
    COST_RISK: str = "cost-risk"
    NEEDS_REVIEW: str = "needs-review"


class RuleId:
    """ReconciliationDelta.rule_id values (api-contracts.yaml enum — full list)."""
    SECRET_REF_WITHOUT_SECRET_ACCESSOR: str = "secret-ref-without-secretAccessor"
    SECRET_DECLARED_NOT_CREATED: str = "secret-declared-not-created"
    PORT_MISMATCH: str = "port-mismatch"
    HOST_NOT_0_0_0_0: str = "host-not-0.0.0.0"
    MISSING_HEALTH_PROBE: str = "missing-health-probe"
    MISSING_STARTUP_PROBE: str = "missing-startup-probe"
    PID1_SIGNAL_UNSAFE: str = "pid1-signal-unsafe"
    OVER_BROAD_SA_ROLE: str = "over-broad-sa-role"
    MISSING_REQUIRED_ROLE: str = "missing-required-role"
    API_NOT_ENABLED: str = "api-not-enabled"
    SCALING_COST_FLAG: str = "scaling-cost-flag"
    UNPINNED_BASE_IMAGE: str = "unpinned-base-image"
    AMBIGUOUS: str = "ambiguous"


class Verdict:
    """ReadinessScorecard.verdict values (api-contracts.yaml)."""
    BLOCKED: str = "BLOCKED"
    WARN: str = "WARN"
    READY: str = "READY"


class PortSource:
    """DockerfileFacts.port_source values (api-contracts.yaml)."""
    DOCKERFILE_ENV: str = "dockerfile-env"
    DOCKERFILE_EXPOSE: str = "dockerfile-expose"
    APP_DEFAULT: str = "app-default"
    UNKNOWN: str = "unknown"


class HostBinding:
    """IntendedContract.host_binding / EntrypointFacts.host_binding values (api-contracts.yaml)."""
    ALL: str = "0.0.0.0"
    LOCALHOST: str = "localhost"
    LOOPBACK: str = "127.0.0.1"
    UNKNOWN: str = "unknown"


class PatchKind:
    """ProposedPatch.kind values (api-contracts.yaml)."""
    GCLOUD_COMMAND: str = "gcloud-command"
    SERVICE_YAML_DIFF: str = "service-yaml-diff"
    DOCKERFILE_DIFF: str = "dockerfile-diff"
    TERRAFORM_DIFF: str = "terraform-diff"


class EvidenceSource:
    """Evidence.source values (api-contracts.yaml)."""
    INTENDED: str = "intended"
    DECLARED: str = "declared"
    LIVE: str = "live"


class Mode:
    """LiveState.mode / gcloud_read.mode values (api-contracts.yaml)."""
    LIVE: str = "live"
    RECORD: str = "record"
    FIXTURE: str = "fixture"


# ---------------------------------------------------------------------------
# Tool fragments
# ---------------------------------------------------------------------------

@dataclass
class Evidence:
    """
    One piece of evidence tracing a finding back to a specific source location.
    api-contracts.yaml: components/schemas/Evidence
    Required: source, locator.  Optional: snippet (REDACTED — no secret values).
    """
    source: str      # EvidenceSource value ("intended" | "declared" | "live")
    locator: str     # "file:line", gcloud resource path, or service.yaml path
    snippet: str = ""  # REDACTED snippet — backend redaction pass runs before population

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Evidence":
        return cls(
            source=d["source"],
            locator=d["locator"],
            snippet=d.get("snippet", ""),
        )


@dataclass
class DockerfileFacts:
    """
    Parsed Dockerfile fragment — output of the parse_dockerfile tool (RepoAuditor).
    api-contracts.yaml: components/schemas/DockerfileFacts
    All required fields present; evidence list always populated (at least one entry).
    """
    port: int | None
    port_source: str             # PortSource value
    pid1_signal_safe: bool       # True only if exec-form CMD/ENTRYPOINT
    base_image_pinned: bool      # True if FROM image@sha256:... or image:tag (not :latest)
    non_root_user: bool          # True if Dockerfile contains USER <non-root>
    secret_refs: list[str]       # Secret Manager secret names referenced via --mount or env
    env_vars: list[str]          # Env var names declared in the Dockerfile
    evidence: list[Evidence]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DockerfileFacts":
        return cls(
            port=d.get("port"),
            port_source=d["port_source"],
            pid1_signal_safe=d["pid1_signal_safe"],
            base_image_pinned=d["base_image_pinned"],
            non_root_user=d["non_root_user"],
            secret_refs=d.get("secret_refs", []),
            env_vars=d.get("env_vars", []),
            evidence=[Evidence.from_dict(e) for e in d.get("evidence", [])],
        )


@dataclass
class EntrypointFacts:
    """
    Inferred runtime binding from app code — output of parse_app_entrypoint (RepoAuditor).
    api-contracts.yaml: components/schemas/EntrypointFacts
    confidence < 1.0 signals ambiguity → escalated to Gemini 2.5 Flash (LLM-02).
    """
    host_binding: str       # HostBinding value
    port: int | None
    confidence: float       # 0.0 – 1.0; < 1.0 = ambiguous, model-classified
    evidence: list[Evidence]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EntrypointFacts":
        return cls(
            host_binding=d["host_binding"],
            port=d.get("port"),
            confidence=d["confidence"],
            evidence=[Evidence.from_dict(e) for e in d.get("evidence", [])],
        )


@dataclass
class CodeMatch:
    """
    One search hit from grep_code — file + line + text evidence.
    api-contracts.yaml: components/schemas/CodeMatch
    """
    file: str
    line: int
    text: str    # REDACTED line text (backend redaction pass must mask secret values)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CodeMatch":
        return cls(file=d["file"], line=d["line"], text=d["text"])


# ---------------------------------------------------------------------------
# Core data shapes (inter-agent session-state handoff)
# ---------------------------------------------------------------------------

@dataclass
class ScalingConfig:
    """
    Nested scaling block inside DeclaredState.
    api-contracts.yaml: DeclaredState.scaling
    """
    min_scale: int | None = None
    max_scale: int | None = None
    concurrency: int | None = None
    cpu_throttling: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "ScalingConfig":
        if not d:
            return cls()
        return cls(
            min_scale=d.get("min_scale"),
            max_scale=d.get("max_scale"),
            concurrency=d.get("concurrency"),
            cpu_throttling=d.get("cpu_throttling"),
        )


@dataclass
class IntendedContract:
    """
    What the code/Dockerfile REQUIRE at runtime (source = Intended).
    Produced by RepoAuditor → written to session state (SessionKeys.INTENDED_CONTRACT).
    Consumed by Reconciler.
    api-contracts.yaml: components/schemas/IntendedContract
    Required fields: port, host_binding, pid1_signal_safe, base_image_pinned,
                     non_root_user, secret_refs, env_vars, expects_health_probe, confidence.
    """
    port: int | None
    host_binding: str        # HostBinding value
    pid1_signal_safe: bool
    base_image_pinned: bool
    non_root_user: bool
    secret_refs: list[str]   # Secret Manager secret names the code expects at runtime
    env_vars: list[str]      # Env vars the code reads
    expects_health_probe: bool
    confidence: float        # Overall confidence; < 1.0 if any field was model-classified

    # Optional fields (api-contracts.yaml marks these without "required")
    expects_startup_probe: bool = False
    required_apis: list[str] = field(default_factory=list)  # e.g. "aiplatform.googleapis.com"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "IntendedContract":
        return cls(
            port=d.get("port"),
            host_binding=d["host_binding"],
            pid1_signal_safe=d["pid1_signal_safe"],
            base_image_pinned=d["base_image_pinned"],
            non_root_user=d["non_root_user"],
            secret_refs=d.get("secret_refs", []),
            env_vars=d.get("env_vars", []),
            expects_health_probe=d["expects_health_probe"],
            confidence=d["confidence"],
            expects_startup_probe=d.get("expects_startup_probe", False),
            required_apis=d.get("required_apis", []),
        )


@dataclass
class DeclaredState:
    """
    What the deploy config DECLARES (source = Declared; from service.yaml/cloudbuild/workflow).
    Produced by the Declared parser (runs under Orchestrator, BE-02) → session state.
    Consumed by Reconciler.
    api-contracts.yaml: components/schemas/DeclaredState
    Required fields: container_port, secret_refs, env_vars, has_liveness_probe,
                     has_startup_probe, scaling.
    """
    container_port: int | None
    secret_refs: list[str]        # secretKeyRef names declared in the deploy config
    env_vars: list[str]
    has_liveness_probe: bool
    has_startup_probe: bool
    scaling: ScalingConfig

    # Optional fields
    service_name: str = ""
    service_account: str | None = None
    templated_unresolved: list[str] = field(default_factory=list)  # ${ENV} placeholders

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DeclaredState":
        return cls(
            container_port=d.get("container_port"),
            secret_refs=d.get("secret_refs", []),
            env_vars=d.get("env_vars", []),
            has_liveness_probe=d["has_liveness_probe"],
            has_startup_probe=d["has_startup_probe"],
            scaling=ScalingConfig.from_dict(d.get("scaling")),
            service_name=d.get("service_name", ""),
            service_account=d.get("service_account"),
            templated_unresolved=d.get("templated_unresolved", []),
        )


@dataclass
class SecretAccessorEntry:
    """
    One secret + its accessor members in LiveState.secrets[].
    api-contracts.yaml: LiveState.secrets items.
    accessor_members lists principals holding secretAccessor — NEVER secret values.
    """
    name: str
    accessor_members: list[str]  # e.g. ["serviceAccount:sa@project.iam.gserviceaccount.com"]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SecretAccessorEntry":
        return cls(name=d["name"], accessor_members=d.get("accessor_members", []))


@dataclass
class LiveState:
    """
    What GCP ACTUALLY GRANTS (source = Live; read-only gcloud or fixture replay).
    Produced by GcpStateInspector → written to session state (SessionKeys.LIVE_STATE).
    Consumed by Reconciler.
    api-contracts.yaml: components/schemas/LiveState
    Required fields: project_id, runtime_sa, sa_iam_roles, enabled_apis, secrets, mode.
    Values are NEVER stored — only membership/presence information.
    """
    project_id: str
    runtime_sa: str | None       # Service Account email running the Cloud Run service
    sa_iam_roles: list[str]      # IAM roles bound to runtime_sa on the project
    enabled_apis: list[str]      # Enabled API hostnames, e.g. "run.googleapis.com"
    secrets: list[SecretAccessorEntry]  # Secret Manager secrets + who has secretAccessor
    mode: str                    # Mode value: "live" | "record" | "fixture"

    # Optional: snapshot of the existing Cloud Run service configuration
    run_config: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "runtime_sa": self.runtime_sa,
            "sa_iam_roles": self.sa_iam_roles,
            "enabled_apis": self.enabled_apis,
            "secrets": [s.to_dict() for s in self.secrets],
            "mode": self.mode,
            "run_config": self.run_config,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LiveState":
        return cls(
            project_id=d["project_id"],
            runtime_sa=d.get("runtime_sa"),
            sa_iam_roles=d.get("sa_iam_roles", []),
            enabled_apis=d.get("enabled_apis", []),
            secrets=[SecretAccessorEntry.from_dict(s) for s in d.get("secrets", [])],
            mode=d["mode"],
            run_config=d.get("run_config"),
        )


@dataclass
class ReconciliationDelta:
    """
    One classified discrepancy across the three sources (Reconciler output).
    Written to session state (SessionKeys.RECONCILIATION_DELTAS) as a list.
    Consumed by FixWriter.
    api-contracts.yaml: components/schemas/ReconciliationDelta
    Required: rule_id, delta_class, confidence, summary, evidence (minItems=1).

    IMPORTANT field names (must match contract exactly):
      rule_id     — RuleId constant string (e.g. "secret-ref-without-secretAccessor")
      delta_class — DeltaClass constant string (e.g. "will-fail")
      confidence  — 0.0–1.0; low confidence → DeltaClass.NEEDS_REVIEW (AI Principles §8)
      summary     — REDACTED human-readable summary (backend redaction pass before persist)
      evidence    — at least 1 Evidence entry (which source + locator + REDACTED snippet)
    """
    rule_id: str          # RuleId value
    delta_class: str      # DeltaClass value
    confidence: float     # 0.0 – 1.0
    summary: str          # REDACTED — no secret values
    evidence: list[Evidence]

    # Optional
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "delta_class": self.delta_class,
            "confidence": self.confidence,
            "summary": self.summary,
            "evidence": [e.to_dict() for e in self.evidence],
            "recommendation": self.recommendation,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReconciliationDelta":
        return cls(
            rule_id=d["rule_id"],
            delta_class=d["delta_class"],
            confidence=d["confidence"],
            summary=d["summary"],
            evidence=[Evidence.from_dict(e) for e in d.get("evidence", [])],
            recommendation=d.get("recommendation", ""),
        )


@dataclass
class ProposedPatch:
    """
    A proposed fix generated by FixWriter — NEVER executed by the agent.
    api-contracts.yaml: components/schemas/ProposedPatch
    applied is ALWAYS False (informational only; AI Operating Principles §2).
    """
    rule_id: str     # Must match the ReconciliationDelta.rule_id it addresses
    kind: str        # PatchKind value
    content: str     # The exact diff / gcloud command for the human to apply
    applied: bool = False  # Always False — agent never executes a fix (§2)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Enforce the invariant: applied is always False regardless of what was passed
        d["applied"] = False
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProposedPatch":
        patch = cls(
            rule_id=d["rule_id"],
            kind=d["kind"],
            content=d["content"],
            applied=False,  # Always False — never trust serialized value
        )
        return patch


@dataclass
class ScorecardCounts:
    """
    Counts of deltas by class inside ReadinessScorecard.
    api-contracts.yaml: ReadinessScorecard.counts
    Field names use underscores (Python) — to_dict() uses underscores matching contract.
    Note: contract uses "will_fail" / "will_misbehave" / "cost_risk" / "needs_review"
    (underscores, not hyphens) in the JSON counts block.
    """
    will_fail: int = 0
    will_misbehave: int = 0
    cost_risk: int = 0
    needs_review: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ScorecardCounts":
        return cls(
            will_fail=d.get("will_fail", 0),
            will_misbehave=d.get("will_misbehave", 0),
            cost_risk=d.get("cost_risk", 0),
            needs_review=d.get("needs_review", 0),
        )

    @classmethod
    def from_deltas(cls, deltas: list[ReconciliationDelta]) -> "ScorecardCounts":
        """Compute counts by scanning a list of ReconciliationDelta objects."""
        counts = cls()
        for d in deltas:
            if d.delta_class == DeltaClass.WILL_FAIL:
                counts.will_fail += 1
            elif d.delta_class == DeltaClass.WILL_MISBEHAVE:
                counts.will_misbehave += 1
            elif d.delta_class == DeltaClass.COST_RISK:
                counts.cost_risk += 1
            elif d.delta_class == DeltaClass.NEEDS_REVIEW:
                counts.needs_review += 1
        return counts


@dataclass
class ReadinessScorecard:
    """
    Final pipeline output — verdict + classified delta counts + full delta list + PR URL.
    Written to session state (SessionKeys.READINESS_SCORECARD) by FixWriter.
    api-contracts.yaml: components/schemas/ReadinessScorecard
    Required: verdict, counts, deltas.

    Verdict rule (deterministic):
      BLOCKED  — any will-fail delta present
      WARN     — no will-fail but will-misbehave or cost-risk present
      READY    — only needs-review or zero deltas
    """
    verdict: str           # Verdict value: "BLOCKED" | "WARN" | "READY"
    counts: ScorecardCounts
    deltas: list[ReconciliationDelta]
    pr_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "counts": self.counts.to_dict(),
            "deltas": [d.to_dict() for d in self.deltas],
            "pr_url": self.pr_url,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReadinessScorecard":
        return cls(
            verdict=d["verdict"],
            counts=ScorecardCounts.from_dict(d["counts"]),
            deltas=[ReconciliationDelta.from_dict(delta) for delta in d.get("deltas", [])],
            pr_url=d.get("pr_url"),
        )

    @classmethod
    def from_deltas(cls, deltas: list[ReconciliationDelta], pr_url: str | None = None) -> "ReadinessScorecard":
        """
        Compute a ReadinessScorecard from a list of classified deltas.
        Verdict rule: BLOCKED if any will-fail; WARN if will-misbehave or cost-risk; else READY.
        """
        counts = ScorecardCounts.from_deltas(deltas)
        if counts.will_fail > 0:
            verdict = Verdict.BLOCKED
        elif counts.will_misbehave > 0 or counts.cost_risk > 0:
            verdict = Verdict.WARN
        else:
            verdict = Verdict.READY
        return cls(verdict=verdict, counts=counts, deltas=deltas, pr_url=pr_url)
