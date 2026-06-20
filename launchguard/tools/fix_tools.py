"""
launchguard.tools.fix_tools — BE-06: FixWriter tools (the delta consumer).

Tools:
    propose_patch(delta, context=None) -> ProposedPatch
        Deterministically map ONE ReconciliationDelta to a concrete fix:
          - secret-ref-without-secretAccessor → gcloud add-iam-policy-binding (PR step)
          - secret-declared-not-created       → gcloud secrets create (PR step)
          - port-mismatch                     → service.yaml containerPort diff
          - host-not-0.0.0.0                  → app/entrypoint host diff
          - missing-health-probe              → service.yaml livenessProbe block
          - missing-startup-probe             → service.yaml startupProbe block
          - pid1-signal-unsafe                → Dockerfile exec-form CMD diff
          - over-broad-sa-role                → gcloud remove + least-privilege grant
          - missing-required-role             → gcloud projects add-iam-policy-binding
          - api-not-enabled                   → gcloud services enable
          - scaling-cost-flag                 → service.yaml scaling annotation diff
          - unpinned-base-image               → Dockerfile FROM pin diff
        applied is ALWAYS False — the agent NEVER executes a fix (AI Principles §2).

    open_pr(repo, branch, title, body, patches, *, mock=None, github_token=None)
        -> dict {pr_url, merged}
        Human-in-the-loop PR creation behind a guardrail gate.
          - DEFAULT mock/dry-run mode (no network): renders the PR body + patches to
            disk under eval/pr_preview/ and returns a synthetic local pr_url.
          - Real mode (mock=False) requires GITHUB_TOKEN and the GitHub MCP / API; in
            this sandbox it is import-safe and raises a clear deferral if invoked
            without a token. NEVER targets the default branch (main/master) — that is a
            409 GUARDRAIL_PR_TARGET_VIOLATION (api-contracts.yaml). NEVER merges.

GUARDRAILS honored here (AI Operating Principles):
    §1 read-only on cloud  : gcloud commands are emitted as TEXT for the human, never run.
    §2 changes via PR only  : propose_patch.applied is always False; open_pr never merges.
    §3 secret redaction     : PR body + patch content pass redact() before any persistence.
    §4 tool allow-listing   : these are the FixWriter-only tools (config.TOOL_ALLOWLISTS).
    §8 fail-safe            : needs-review deltas get an explanatory, non-destructive note.

This module is stdlib-only (+ launchguard internals). It does NOT import google-adk,
google-genai, or any network SDK at module load — it is always importable in CI.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from launchguard.guardrails.audit import get_audit_logger
from launchguard.guardrails.enforce import GuardrailViolation
from launchguard.guardrails.redact import redact
from launchguard.models import (
    DeltaClass,
    PatchKind,
    ProposedPatch,
    ReconciliationDelta,
    RuleId,
)

# ---------------------------------------------------------------------------
# Default branches we must NEVER target with a PR (AI Operating Principles §2)
# ---------------------------------------------------------------------------

_DEFAULT_BRANCHES: frozenset[str] = frozenset({"main", "master", "trunk", "develop"})

# Placeholders used in the generated commands. The human substitutes real values;
# the agent never knows or needs the live values (read-only + redaction discipline).
_PROJECT_PLACEHOLDER = "<PROJECT_ID>"
_SA_PLACEHOLDER = "<RUNTIME_SA_EMAIL>"


# ---------------------------------------------------------------------------
# Typed error for the PR-target guardrail (maps to contract 409)
# ---------------------------------------------------------------------------

class GuardrailPrTargetViolation(GuardrailViolation):
    """
    Raised when open_pr is asked to target a default branch.

    Contract: 409 GUARDRAIL_PR_TARGET_VIOLATION (api-contracts.yaml /tools/open_pr).
    AI Operating Principles §2 — changes go through a reviewable PR, never to main.
    """

    def __init__(self, branch: str) -> None:
        super().__init__(
            code="GUARDRAIL_PR_TARGET_VIOLATION",
            message=(
                f"Refusing to open a PR targeting default branch '{branch}'. "
                f"Fix changes go through a reviewable non-default branch only "
                f"(AI Operating Principles §2)."
            ),
        )
        self.branch = branch


# ---------------------------------------------------------------------------
# propose_patch — deterministic delta → fix mapping
# ---------------------------------------------------------------------------

def propose_patch(
    delta: ReconciliationDelta | dict[str, Any],
    context: dict[str, Any] | None = None,
) -> ProposedPatch:
    """
    Map a single ReconciliationDelta to a concrete, human-applyable ProposedPatch.

    Deterministic (no model call): every rule_id has a hand-authored fix template.
    The LLM-04 layer may later wrap the .content in a richer PR narrative, but the
    actionable command/diff is generated here so it is reproducible and testable
    without Gemini.

    The fix is ALWAYS a *proposal*: ProposedPatch.applied is forced False — the agent
    never executes a gcloud mutation or edits a file (AI Operating Principles §1, §2).

    Args:
        delta:   A ReconciliationDelta (or its to_dict() form).
        context: Optional dict with project_id / runtime_sa / service_name to make the
                 generated command concrete. Values are used only as TEXT substitutions
                 in the command for the human; they are not live credentials.

    Returns:
        ProposedPatch with rule_id, kind, content (the diff/command), applied=False.
    """
    if isinstance(delta, dict):
        delta = ReconciliationDelta.from_dict(delta)

    ctx = context or {}
    project_id = ctx.get("project_id") or _PROJECT_PLACEHOLDER
    runtime_sa = ctx.get("runtime_sa") or _SA_PLACEHOLDER
    # runtime_sa may carry the "serviceAccount:" prefix from LiveState — strip for the
    # --member flag which expects "serviceAccount:<email>" exactly once.
    sa_member = runtime_sa if runtime_sa.startswith("serviceAccount:") else f"serviceAccount:{runtime_sa}"

    secret_name = _extract_secret_name(delta)
    api_name = _extract_api_name(delta)
    role_name = _extract_role_name(delta)

    builder = _PATCH_BUILDERS.get(delta.rule_id, _build_generic_patch)
    kind, content = builder(
        delta=delta,
        project_id=project_id,
        sa_member=sa_member,
        secret_name=secret_name,
        api_name=api_name,
        role_name=role_name,
    )

    # AI Operating Principles §3: redact the content before it ever leaves the function
    # (defensive — content is built from names only, but the redactor is the safety net).
    safe_content = str(redact(content))

    patch = ProposedPatch(
        rule_id=delta.rule_id,
        kind=kind,
        content=safe_content,
        applied=False,  # NEVER True — agent does not execute (§2)
    )

    get_audit_logger().log_tool_call(
        agent_name="FixWriter",
        tool_name="propose_patch",
        input_summary={"rule_id": delta.rule_id, "delta_class": delta.delta_class},
        outcome="ok",
    )
    return patch


def propose_patches(
    deltas: list[ReconciliationDelta] | list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> list[ProposedPatch]:
    """Convenience: map a list of deltas to patches (skips needs-review with a note)."""
    patches: list[ProposedPatch] = []
    for d in deltas:
        patches.append(propose_patch(d, context=context))
    return patches


# ---------------------------------------------------------------------------
# Per-rule patch builders. Each returns (PatchKind, content_str).
# Signature is uniform so they can live in a dispatch table.
# ---------------------------------------------------------------------------

def _build_secret_accessor(*, project_id, sa_member, secret_name, **_):  # type: ignore[no-untyped-def]
    name = secret_name or "<SECRET_NAME>"
    content = (
        f"# Grant the runtime service account secretAccessor on '{name}'.\n"
        f"# Run this yourself after review — LaunchGuard never executes it.\n"
        f"gcloud secrets add-iam-policy-binding {name} \\\n"
        f"  --project={project_id} \\\n"
        f"  --member='{sa_member}' \\\n"
        f"  --role='roles/secretmanager.secretAccessor'"
    )
    return PatchKind.GCLOUD_COMMAND, content


def _build_secret_not_created(*, project_id, secret_name, **_):  # type: ignore[no-untyped-def]
    name = secret_name or "<SECRET_NAME>"
    content = (
        f"# Secret '{name}' is declared in service.yaml but does not exist in Secret Manager.\n"
        f"# Create it, then add a version with the (redacted) value out-of-band.\n"
        f"gcloud secrets create {name} --project={project_id} --replication-policy=automatic\n"
        f"# Then (value supplied by a human, NEVER by LaunchGuard):\n"
        f"#   gcloud secrets versions add {name} --project={project_id} --data-file=-"
    )
    return PatchKind.GCLOUD_COMMAND, content


def _build_port_mismatch(*, delta, **_):  # type: ignore[no-untyped-def]
    intended, declared = _two_ports_from_evidence(delta)
    content = (
        "--- a/infra/cloud-run/service.yaml\n"
        "+++ b/infra/cloud-run/service.yaml\n"
        "@@ spec.template.spec.containers[0].ports[0] @@\n"
        "         ports:\n"
        f"-          - containerPort: {declared or '<DECLARED_PORT>'}\n"
        f"+          - containerPort: {intended or '<INTENDED_PORT>'}\n"
        "# Align the declared containerPort with the port the app actually listens on.\n"
        "# (Or change the app/Dockerfile to listen on the declared port instead.)"
    )
    return PatchKind.SERVICE_YAML_DIFF, content


def _build_host_not_all(**_):  # type: ignore[no-untyped-def]
    content = (
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ uvicorn entrypoint @@\n"
        "-    uvicorn.run(app, host='127.0.0.1', port=PORT)\n"
        "+    uvicorn.run(app, host='0.0.0.0', port=PORT)\n"
        "# Cloud Run's proxy cannot reach a loopback bind; bind all interfaces."
    )
    return PatchKind.DOCKERFILE_DIFF, content  # closest kind (app/entrypoint edit)


def _build_missing_health(**_):  # type: ignore[no-untyped-def]
    content = (
        "--- a/infra/cloud-run/service.yaml\n"
        "+++ b/infra/cloud-run/service.yaml\n"
        "@@ spec.template.spec.containers[0] @@\n"
        "+          livenessProbe:\n"
        "+            httpGet:\n"
        "+              path: /health\n"
        "+              port: 8080\n"
        "+            initialDelaySeconds: 10\n"
        "+            periodSeconds: 10\n"
        "# Add a livenessProbe so Cloud Run can detect a stuck container."
    )
    return PatchKind.SERVICE_YAML_DIFF, content


def _build_missing_startup(**_):  # type: ignore[no-untyped-def]
    content = (
        "--- a/infra/cloud-run/service.yaml\n"
        "+++ b/infra/cloud-run/service.yaml\n"
        "@@ spec.template.spec.containers[0] @@\n"
        "+          startupProbe:\n"
        "+            httpGet:\n"
        "+              path: /ready\n"
        "+              port: 8080\n"
        "+            initialDelaySeconds: 5\n"
        "+            periodSeconds: 5\n"
        "+            failureThreshold: 12\n"
        "# Add a startupProbe so Cloud Run waits for readiness before routing traffic."
    )
    return PatchKind.SERVICE_YAML_DIFF, content


def _build_pid1(**_):  # type: ignore[no-untyped-def]
    content = (
        "--- a/Dockerfile\n"
        "+++ b/Dockerfile\n"
        "@@ final stage CMD @@\n"
        "-CMD python -m uvicorn app:app --host 0.0.0.0 --port 8080\n"
        '+CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]\n'
        "# Exec-form CMD makes the process PID 1 directly so it receives SIGTERM\n"
        "# (shell-form wraps it in /bin/sh which swallows the signal)."
    )
    return PatchKind.DOCKERFILE_DIFF, content


def _build_over_broad_sa(*, project_id, sa_member, role_name, **_):  # type: ignore[no-untyped-def]
    role = role_name or "roles/editor"
    content = (
        f"# The runtime SA holds the over-broad role '{role}'. Remove it and grant\n"
        f"# only the least-privilege roles the service actually needs.\n"
        f"gcloud projects remove-iam-policy-binding {project_id} \\\n"
        f"  --member='{sa_member}' \\\n"
        f"  --role='{role}'\n"
        f"# Then grant the minimal set, e.g.:\n"
        f"gcloud projects add-iam-policy-binding {project_id} \\\n"
        f"  --member='{sa_member}' \\\n"
        f"  --role='roles/run.invoker'"
    )
    return PatchKind.GCLOUD_COMMAND, content


def _build_missing_required_role(*, project_id, sa_member, role_name, **_):  # type: ignore[no-untyped-def]
    role = role_name or "<REQUIRED_ROLE>"
    content = (
        f"# The code needs an API whose access requires '{role}', which the runtime SA lacks.\n"
        f"gcloud projects add-iam-policy-binding {project_id} \\\n"
        f"  --member='{sa_member}' \\\n"
        f"  --role='{role}'"
    )
    return PatchKind.GCLOUD_COMMAND, content


def _build_api_not_enabled(*, project_id, api_name, **_):  # type: ignore[no-untyped-def]
    api = api_name or "<API>.googleapis.com"
    content = (
        f"# Required API '{api}' is not enabled in the project.\n"
        f"gcloud services enable {api} --project={project_id}"
    )
    return PatchKind.GCLOUD_COMMAND, content


def _build_scaling(**_):  # type: ignore[no-untyped-def]
    content = (
        "--- a/infra/cloud-run/service.yaml\n"
        "+++ b/infra/cloud-run/service.yaml\n"
        "@@ spec.template.metadata.annotations @@\n"
        "-        autoscaling.knative.dev/maxScale: \"1000\"\n"
        "+        autoscaling.knative.dev/maxScale: \"10\"\n"
        "-        run.googleapis.com/cpu-throttling: \"false\"\n"
        "+        run.googleapis.com/cpu-throttling: \"true\"\n"
        "# Cap maxScale to a realistic ceiling and throttle idle CPU to control cost.\n"
        "# (Advisory — review against your real traffic before applying.)"
    )
    return PatchKind.SERVICE_YAML_DIFF, content


def _build_unpinned_base(**_):  # type: ignore[no-untyped-def]
    content = (
        "--- a/Dockerfile\n"
        "+++ b/Dockerfile\n"
        "@@ base image @@\n"
        "-FROM python:latest\n"
        "+FROM python:3.12-slim\n"
        "# Pin to a specific tag (or @sha256 digest) for reproducible builds."
    )
    return PatchKind.DOCKERFILE_DIFF, content


def _build_needs_review(*, delta, **_):  # type: ignore[no-untyped-def]
    content = (
        f"# NEEDS REVIEW — confidence {delta.confidence:.2f}. LaunchGuard did not classify this\n"
        f"# with enough certainty to propose a destructive fix (AI Operating Principles §8:\n"
        f"# prefer a false negative over a confident-but-wrong recommendation).\n"
        f"# Summary: {delta.summary}\n"
        f"# A human should review the evidence and decide.\n"
        f"# (No automated command/diff is proposed for a needs-review delta.)"
    )
    return PatchKind.GCLOUD_COMMAND, content


def _build_generic_patch(*, delta, **_):  # type: ignore[no-untyped-def]
    if delta.delta_class == DeltaClass.NEEDS_REVIEW:
        return _build_needs_review(delta=delta)
    content = (
        f"# No specific fix template for rule '{delta.rule_id}'.\n"
        f"# Recommendation from the reconciler:\n"
        f"# {delta.recommendation or delta.summary}"
    )
    return PatchKind.GCLOUD_COMMAND, content


_PATCH_BUILDERS: dict[str, Callable[..., tuple[str, str]]] = {
    RuleId.SECRET_REF_WITHOUT_SECRET_ACCESSOR: _build_secret_accessor,
    RuleId.SECRET_DECLARED_NOT_CREATED: _build_secret_not_created,
    RuleId.PORT_MISMATCH: _build_port_mismatch,
    RuleId.HOST_NOT_0_0_0_0: _build_host_not_all,
    RuleId.MISSING_HEALTH_PROBE: _build_missing_health,
    RuleId.MISSING_STARTUP_PROBE: _build_missing_startup,
    RuleId.PID1_SIGNAL_UNSAFE: _build_pid1,
    RuleId.OVER_BROAD_SA_ROLE: _build_over_broad_sa,
    RuleId.MISSING_REQUIRED_ROLE: _build_missing_required_role,
    RuleId.API_NOT_ENABLED: _build_api_not_enabled,
    RuleId.SCALING_COST_FLAG: _build_scaling,
    RuleId.UNPINNED_BASE_IMAGE: _build_unpinned_base,
    RuleId.AMBIGUOUS: _build_needs_review,
}


# ---------------------------------------------------------------------------
# Evidence extraction helpers (names only — never values)
# ---------------------------------------------------------------------------

def _extract_secret_name(delta: ReconciliationDelta) -> str | None:
    """Pull a secret NAME out of the delta evidence/summary (never a value)."""
    for ev in delta.evidence:
        # locator like "secretmanager/JWT_SECRET_KEY/iam-policy"
        if ev.locator.startswith("secretmanager/"):
            parts = ev.locator.split("/")
            if len(parts) >= 2 and parts[1]:
                return parts[1]
        # snippet like "secretKeyRef.name=JWT_SECRET_KEY"
        if "secretKeyRef.name=" in ev.snippet:
            return ev.snippet.split("secretKeyRef.name=", 1)[1].strip().rstrip(")")
    # Fallback: parse "Secret 'NAME'" from the summary
    if "Secret '" in delta.summary:
        return delta.summary.split("Secret '", 1)[1].split("'", 1)[0]
    return None


def _extract_api_name(delta: ReconciliationDelta) -> str | None:
    for ev in delta.evidence:
        if "required_apis=['" in ev.snippet:
            return ev.snippet.split("required_apis=['", 1)[1].split("'", 1)[0]
        if ev.locator.endswith("/enabled-apis") and ":" in ev.snippet:
            return ev.snippet.split(":", 1)[0].strip()
    return None


def _extract_role_name(delta: ReconciliationDelta) -> str | None:
    for ev in delta.evidence:
        if "sa_iam_roles contains " in ev.snippet:
            return ev.snippet.split("sa_iam_roles contains ", 1)[1].strip()
        if "does not contain " in ev.snippet:
            return ev.snippet.split("does not contain ", 1)[1].strip()
    # recommendation often names the role explicitly
    if "roles/" in delta.recommendation:
        frag = delta.recommendation.split("roles/", 1)[1]
        token = frag.split("'")[0].split()[0].split("\\")[0].strip()
        return f"roles/{token}" if token else None
    return None


def _two_ports_from_evidence(delta: ReconciliationDelta) -> tuple[int | None, int | None]:
    """Return (intended_port, declared_port) parsed from port-mismatch evidence."""
    intended = declared = None
    for ev in delta.evidence:
        if ev.snippet.startswith("port=") and ev.source == "intended":
            intended = _safe_int(ev.snippet.split("=", 1)[1])
        if ev.snippet.startswith("containerPort=") and ev.source == "declared":
            declared = _safe_int(ev.snippet.split("=", 1)[1])
    return intended, declared


def _safe_int(s: str) -> int | None:
    try:
        return int(s.strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# open_pr — human-in-the-loop PR creation (mock dry-run default)
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    # launchguard/tools/fix_tools.py → launchguard/ → repo root
    return Path(__file__).resolve().parents[2]


def _redact_text_block(text: str) -> str:
    """
    Redact a multi-line text block LINE BY LINE.

    The shared redactor's `keyword=VALUE` regex is greedy and, applied to a whole
    rendered Markdown body, can span a line boundary and consume adjacent markup
    (e.g. eat a `**Proposed fix**` header that follows an evidence line ending in
    `...name=JWT_SECRET_KEY`). Redacting per line bounds every regex match to a single
    line so the redaction guarantee holds WITHOUT corrupting document structure.
    """
    return "\n".join(str(redact(line)) for line in text.split("\n"))


def open_pr(
    repo: str,
    branch: str,
    title: str,
    body: str,
    patches: list[ProposedPatch] | list[dict[str, Any]],
    *,
    mock: bool | None = None,
    github_token: str | None = None,
) -> dict[str, Any]:
    """
    Open a fix-PR (human merge gate). Default = mock/dry-run, no network.

    Guardrails (AI Operating Principles §2):
      - branch MUST NOT be a default branch → GuardrailPrTargetViolation (409).
      - merged is ALWAYS False — the agent never merges.
      - In real mode, the agent only CREATES a PR; it stops and surfaces the URL.

    Mode selection:
      - mock is None  → resolve from env LAUNCHGUARD_PR_MODE ("real" → live, else mock).
                        Absent network creds → mock. This makes CI deterministic + offline.
      - mock is True  → dry-run: render the PR body + patches to eval/pr_preview/<branch>.md
                        and return a synthetic local pr_url.
      - mock is False → real mode: requires github_token (or GITHUB_TOKEN). In this sandbox
                        no token is present, so it raises a clear deferral (network machine).

    Args:
        repo:    "owner/name" GitHub repo slug.
        branch:  Non-default branch for the PR head.
        title:   PR title (redacted before persistence).
        body:    PR body / readiness narrative (redacted before persistence).
        patches: ProposedPatch objects (or their dicts) to include in the PR.
        mock:    Override mode (see above).
        github_token: Real-mode token; falls back to env GITHUB_TOKEN.

    Returns:
        {"pr_url": str, "merged": False}

    Raises:
        GuardrailPrTargetViolation: if branch is a default branch.
        RuntimeError: real mode requested but no token / network deferral.
    """
    logger = get_audit_logger()

    # ---- Guardrail: never target a default branch (§2) -------------------- #
    if branch.strip().lower() in _DEFAULT_BRANCHES:
        logger.log_guardrail_trip(
            code="GUARDRAIL_PR_TARGET_VIOLATION",
            detail=f"attempted PR target branch='{branch}'",
            agent_name="FixWriter",
        )
        raise GuardrailPrTargetViolation(branch=branch)

    # ---- Resolve mode ----------------------------------------------------- #
    resolved_mock = _resolve_pr_mock(mock)
    token = github_token or os.environ.get("GITHUB_TOKEN")

    # ---- Normalize patches to ProposedPatch (enforces applied=False) ------ #
    norm_patches = [
        p if isinstance(p, ProposedPatch) else ProposedPatch.from_dict(p)
        for p in patches
    ]

    # ---- Redact before ANY persistence/network (§3) ----------------------- #
    # Line-bounded so the greedy keyword=VALUE regex cannot span lines and corrupt the
    # rendered Markdown structure (see _redact_text_block).
    safe_title = str(redact(title))
    safe_body = _redact_text_block(body)

    if resolved_mock:
        pr_url = _render_pr_preview(repo, branch, safe_title, safe_body, norm_patches)
        logger.log_tool_call(
            agent_name="FixWriter",
            tool_name="open_pr",
            input_summary={"repo": repo, "branch": branch, "mode": "mock", "patches": len(norm_patches)},
            outcome="ok",
        )
        return {"pr_url": pr_url, "merged": False}

    # ---- Real mode (deferred to a network machine) ------------------------ #
    if not token:
        logger.log_tool_call(
            agent_name="FixWriter",
            tool_name="open_pr",
            input_summary={"repo": repo, "branch": branch, "mode": "real", "token": False},
            outcome="error",
        )
        raise RuntimeError(
            "open_pr real mode requires GITHUB_TOKEN and the GitHub MCP / API. "
            "No token is configured in this environment — real PR creation is DEFERRED "
            "to a network machine. Use mock=True (default) for offline dry-run."
        )
    # A token is present but the GitHub MCP integration itself is the network-machine
    # deliverable (DEMO-01 adjacent). Keep this seam explicit and fail loud rather than
    # half-create a PR.
    raise RuntimeError(
        "open_pr real mode: GITHUB_TOKEN present, but live GitHub MCP PR creation is "
        "wired only on the network machine. This is the documented integration seam."
    )


def _resolve_pr_mock(mock: bool | None) -> bool:
    """Decide mock vs real. Default to mock unless explicitly opted into real."""
    if mock is not None:
        return mock
    mode = os.environ.get("LAUNCHGUARD_PR_MODE", "mock").strip().lower()
    return mode != "real"


def _render_pr_preview(
    repo: str,
    branch: str,
    title: str,
    body: str,
    patches: list[ProposedPatch],
) -> str:
    """
    Render a dry-run PR preview (body + patches) to disk and return a local pr_url.

    Output: eval/pr_preview/<branch>.md  (created; redacted content only).
    The returned pr_url is a synthetic local-file URI so downstream code (scorecard,
    session state) has a stable, non-network reference in offline mode.
    """
    preview_dir = _repo_root() / "eval" / "pr_preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    safe_branch = branch.replace("/", "_")
    out_path = preview_dir / f"{safe_branch}.md"

    lines: list[str] = [
        f"# {title}",
        "",
        f"- **Repo:** `{repo}`",
        f"- **Branch:** `{branch}` (non-default — human merge gate)",
        "- **Mode:** DRY-RUN (mock) — no network, no live mutation, never merged",
        "",
        "## PR Body",
        "",
        body,
        "",
        "## Proposed Patches",
        "",
        "> Every patch is a *proposal*. LaunchGuard never executes any of these "
        "(AI Operating Principles §1, §2). Apply them yourself after review.",
        "",
    ]
    for i, p in enumerate(patches, start=1):
        lines.append(f"### {i}. `{p.rule_id}` ({p.kind})")
        lines.append("")
        lines.append("```")
        lines.append(p.content)
        lines.append("```")
        lines.append(f"_applied={p.applied} (always false — never executed)_")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path.as_uri()


# Also emit a machine-readable JSON sidecar for the scorecard / tests.
def render_pr_payload_json(
    repo: str,
    branch: str,
    title: str,
    body: str,
    patches: list[ProposedPatch],
) -> dict[str, Any]:
    """Return the redacted PR payload as a JSON-serializable dict (for tests/scorecard)."""
    return {
        "repo": repo,
        "branch": branch,
        "title": str(redact(title)),
        "body": str(redact(body)),
        "merged": False,
        "patches": [p.to_dict() for p in patches],
    }


__all__ = [
    "GuardrailPrTargetViolation",
    "open_pr",
    "propose_patch",
    "propose_patches",
    "render_pr_payload_json",
]
