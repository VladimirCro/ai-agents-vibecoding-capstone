"""
launchguard.reconciler.engine — BE-05: Deterministic Reconciler rule engine (CORE IP).

reconcile(intended, declared, live) -> list[ReconciliationDelta]

This module is the CORE IP of LaunchGuard.  It:
  - Receives three normalized state objects (IntendedContract, DeclaredState, LiveState)
  - Applies 11 deterministic detector rules
  - Emits a ReconciliationDelta per discrepancy with:
      rule_id, delta_class, confidence, summary (REDACTED), evidence (>=1), recommendation

CRITICAL design constraints (enforced by architecture.md + AI Operating Principles):
  1. NO external tools — no network, no file I/O, no subprocess
  2. NO model calls — pure Python logic over dataclass inputs
  3. High confidence (0.95–1.0) only for deterministic hits
  4. Low confidence → DeltaClass.NEEDS_REVIEW (§8 fail-safe)
  5. Secret values NEVER appear in summary/recommendation/evidence snippets

Detector rules implemented (11 total):
  1.  secret-ref-without-secretAccessor [KILLER, will-fail, conf=0.98]
  2.  secret-declared-not-created        [will-fail, conf=0.97]
  3.  port-mismatch                      [will-misbehave, conf=1.0]
  4.  host-not-0.0.0.0                   [will-misbehave, conf=1.0]
  5.  missing-health-probe               [will-misbehave, conf=0.95]
  6.  missing-startup-probe              [will-misbehave, conf=0.95]
  7.  pid1-signal-unsafe                 [will-misbehave, conf=1.0]
  8.  over-broad-sa-role                 [will-misbehave, conf=1.0]
  9.  missing-required-role              [will-fail, conf=0.90]
  10. api-not-enabled                    [will-fail, conf=0.90]
  11. scaling-cost-flag                  [cost-risk, conf=0.85]
  12. unpinned-base-image                [will-misbehave advisory, conf=0.90]

LLM-03 seam (DEFERRED): after deterministic rules, ambiguous residue would be sent
to Gemini 2.5 Pro for classification → delta_class=needs-review. Left as a TODO
comment so the seam is visible without changing the deterministic core.
"""

from __future__ import annotations

from launchguard.models import (
    DeclaredState,
    DeltaClass,
    Evidence,
    EvidenceSource,
    HostBinding,
    IntendedContract,
    LiveState,
    ReconciliationDelta,
    RuleId,
)

# ---------------------------------------------------------------------------
# Role → required API implication map
# (used by missing-required-role and api-not-enabled detectors)
# ---------------------------------------------------------------------------

_API_TO_REQUIRED_ROLE: dict[str, str] = {
    "aiplatform.googleapis.com": "roles/aiplatform.user",
    "run.googleapis.com": "roles/run.invoker",
    "secretmanager.googleapis.com": "roles/secretmanager.secretAccessor",
    "storage.googleapis.com": "roles/storage.objectViewer",
    "pubsub.googleapis.com": "roles/pubsub.subscriber",
    "bigquery.googleapis.com": "roles/bigquery.dataViewer",
    "cloudfunctions.googleapis.com": "roles/cloudfunctions.invoker",
}

# Roles that are considered over-broad (owner or editor = full project access)
_OVERBROAD_ROLES: frozenset[str] = frozenset({
    "roles/owner",
    "roles/editor",
})

# Scaling thresholds for cost flag
_SCALING_HIGH_CONCURRENCY_THRESHOLD = 200  # max_scale * concurrency
_SCALING_HIGH_MIN_SCALE = 10


# ---------------------------------------------------------------------------
# Main reconcile function
# ---------------------------------------------------------------------------

def reconcile(
    intended: IntendedContract,
    declared: DeclaredState,
    live: LiveState,
) -> list[ReconciliationDelta]:
    """
    Run all deterministic detector rules over the three normalized state objects.

    Returns a list of ReconciliationDelta, each with:
      - rule_id matching api-contracts.yaml enum
      - delta_class: will-fail / will-misbehave / cost-risk / needs-review
      - confidence: 0.0–1.0 (deterministic hits = high)
      - summary: REDACTED human-readable description (no secret values)
      - evidence: list of at least 1 Evidence entry
      - recommendation: actionable fix description

    NO external tools, NO model calls, NO I/O — pure logic (architecture.md §7).

    TODO (LLM-03, Increment 2): After this function completes, the Reconciler sub-agent
    should pass the delta list + any ambiguous input slices to Gemini 2.5 Pro for
    ambiguity classification. New deltas from that pass use delta_class=needs-review.
    Confidence from model must be < 1.0 (AI Operating Principles §8).

    Args:
        intended: IntendedContract from RepoAuditor (what the code requires).
        declared: DeclaredState from Declared parser (what service.yaml declares).
        live:     LiveState from GcpStateInspector (what GCP actually grants).

    Returns:
        List of ReconciliationDelta (may be empty = READY state).
    """
    deltas: list[ReconciliationDelta] = []

    # Build a lookup map for live secrets: name → SecretAccessorEntry
    live_secrets_by_name = {s.name: s for s in live.secrets}

    # Union of intended + declared secret refs (the full set we need to check)
    all_secret_names: set[str] = set(intended.secret_refs) | set(declared.secret_refs)

    # ------------------------------------------------------------------ #
    # Rule 1: secret-ref-without-secretAccessor [KILLER, will-fail]
    # ------------------------------------------------------------------ #
    # A secret is referenced by the code/config, exists in SM, but the runtime SA
    # does NOT have secretAccessor on it → deploy will fail at secret mount time.
    runtime_sa = live.runtime_sa

    for secret_name in sorted(all_secret_names):
        if secret_name not in live_secrets_by_name:
            # Secret doesn't exist at all → handled by rule 2 below
            continue

        secret_entry = live_secrets_by_name[secret_name]
        if runtime_sa is None:
            # No SA configured → can't check accessors; note as needs-review
            continue

        # Check if runtime_sa is in accessor_members for this secret
        sa_has_access = any(
            runtime_sa in member for member in secret_entry.accessor_members
        )

        if not sa_has_access:
            source_note = _secret_source_note(secret_name, intended, declared)
            deltas.append(ReconciliationDelta(
                rule_id=RuleId.SECRET_REF_WITHOUT_SECRET_ACCESSOR,
                delta_class=DeltaClass.WILL_FAIL,
                confidence=0.98,
                summary=(
                    f"Secret '{secret_name}' is referenced {source_note} but the runtime "
                    f"service account does not have roles/secretmanager.secretAccessor on it. "
                    f"The service will fail to start when Cloud Run attempts to inject the secret."
                ),
                evidence=[
                    Evidence(
                        source=EvidenceSource.LIVE,
                        locator=f"secretmanager/{secret_name}/iam-policy",
                        snippet=f"accessor_members={secret_entry.accessor_members!r}",
                    ),
                    Evidence(
                        source=EvidenceSource.DECLARED,
                        locator="service.yaml/spec.template.spec.containers.env",
                        snippet=f"secretKeyRef.name={secret_name}",
                    ),
                ],
                recommendation=(
                    f"Grant roles/secretmanager.secretAccessor on secret '{secret_name}' "
                    f"to the runtime service account. Run:\n"
                    f"  gcloud secrets add-iam-policy-binding {secret_name} \\\n"
                    f"    --project=<PROJECT_ID> \\\n"
                    f"    --member='serviceAccount:<SA_EMAIL>' \\\n"
                    f"    --role='roles/secretmanager.secretAccessor'"
                ),
            ))

    # ------------------------------------------------------------------ #
    # Rule 2: secret-declared-not-created [will-fail]
    # ------------------------------------------------------------------ #
    # A secret is declared in service.yaml (secretKeyRef) but does not exist
    # in Secret Manager at all → Cloud Run will fail to inject it.
    for secret_name in sorted(declared.secret_refs):
        if secret_name not in live_secrets_by_name:
            deltas.append(ReconciliationDelta(
                rule_id=RuleId.SECRET_DECLARED_NOT_CREATED,
                delta_class=DeltaClass.WILL_FAIL,
                confidence=0.97,
                summary=(
                    f"Secret '{secret_name}' is declared in service.yaml as a secretKeyRef "
                    f"but does not exist in GCP Secret Manager for this project. "
                    f"Cloud Run will fail to inject it at startup."
                ),
                evidence=[
                    Evidence(
                        source=EvidenceSource.DECLARED,
                        locator="service.yaml/spec.template.spec.containers.env",
                        snippet=f"secretKeyRef.name={secret_name}",
                    ),
                    Evidence(
                        source=EvidenceSource.LIVE,
                        locator=f"secretmanager/{secret_name}",
                        snippet="(secret not found in Live state)",
                    ),
                ],
                recommendation=(
                    f"Create the secret in Secret Manager: "
                    f"gcloud secrets create {secret_name} --project=<PROJECT_ID> "
                    f"then add a version with the secret value."
                ),
            ))

    # ------------------------------------------------------------------ #
    # Rule 3: port-mismatch [will-misbehave]
    # ------------------------------------------------------------------ #
    # The port exposed by the Dockerfile/app doesn't match the containerPort
    # declared in service.yaml → Cloud Run routes traffic to the wrong port.
    if (
        intended.port is not None
        and declared.container_port is not None
        and intended.port != declared.container_port
    ):
        deltas.append(ReconciliationDelta(
            rule_id=RuleId.PORT_MISMATCH,
            delta_class=DeltaClass.WILL_MISBEHAVE,
            confidence=1.0,
            summary=(
                f"Port mismatch: the application intends to listen on port {intended.port} "
                f"(from Dockerfile) but service.yaml declares containerPort={declared.container_port}. "
                f"Cloud Run will send traffic to port {declared.container_port} but the "
                f"container listens on {intended.port} → health checks fail, 503 errors."
            ),
            evidence=[
                Evidence(
                    source=EvidenceSource.INTENDED,
                    locator="Dockerfile",
                    snippet=f"port={intended.port}",
                ),
                Evidence(
                    source=EvidenceSource.DECLARED,
                    locator="service.yaml/spec.template.spec.containers.ports[0].containerPort",
                    snippet=f"containerPort={declared.container_port}",
                ),
            ],
            recommendation=(
                f"Align the container port: set containerPort in service.yaml to {intended.port} "
                f"OR update the application/Dockerfile to use port {declared.container_port}."
            ),
        ))

    # ------------------------------------------------------------------ #
    # Rule 4: host-not-0.0.0.0 [will-misbehave]
    # ------------------------------------------------------------------ #
    # App binds to localhost/127.0.0.1 → Cloud Run's proxy can't reach the container
    # (Cloud Run expects the app to listen on 0.0.0.0 to accept routed traffic).
    if intended.host_binding in (HostBinding.LOCALHOST, HostBinding.LOOPBACK):
        deltas.append(ReconciliationDelta(
            rule_id=RuleId.HOST_NOT_0_0_0_0,
            delta_class=DeltaClass.WILL_MISBEHAVE,
            confidence=1.0,
            summary=(
                f"The application binds to '{intended.host_binding}' (loopback), not '0.0.0.0'. "
                f"Cloud Run's sidecar proxy cannot route traffic to a loopback address → "
                f"all requests will time out (502/503)."
            ),
            evidence=[
                Evidence(
                    source=EvidenceSource.INTENDED,
                    locator="app-entrypoint",
                    snippet=f"host_binding={intended.host_binding}",
                ),
            ],
            recommendation=(
                "Update the application to bind to '0.0.0.0' (all interfaces). "
                "Example (uvicorn): uvicorn.run(app, host='0.0.0.0', port=8080)"
            ),
        ))

    # ------------------------------------------------------------------ #
    # Rule 5: missing-health-probe [will-misbehave]
    # ------------------------------------------------------------------ #
    if intended.expects_health_probe and not declared.has_liveness_probe:
        deltas.append(ReconciliationDelta(
            rule_id=RuleId.MISSING_HEALTH_PROBE,
            delta_class=DeltaClass.WILL_MISBEHAVE,
            confidence=0.95,
            summary=(
                "The application exposes a /health endpoint (detected in code) but "
                "service.yaml does not configure a livenessProbe. Cloud Run will not "
                "use the health endpoint to detect stuck containers."
            ),
            evidence=[
                Evidence(
                    source=EvidenceSource.INTENDED,
                    locator="app-code",
                    snippet="expects_health_probe=True (/health route detected)",
                ),
                Evidence(
                    source=EvidenceSource.DECLARED,
                    locator="service.yaml/spec.template.spec.containers",
                    snippet="livenessProbe: (absent)",
                ),
            ],
            recommendation=(
                "Add a livenessProbe to service.yaml:\n"
                "  livenessProbe:\n"
                "    httpGet:\n"
                "      path: /health\n"
                "      port: 8080"
            ),
        ))

    # ------------------------------------------------------------------ #
    # Rule 6: missing-startup-probe [will-misbehave]
    # ------------------------------------------------------------------ #
    if intended.expects_startup_probe and not declared.has_startup_probe:
        deltas.append(ReconciliationDelta(
            rule_id=RuleId.MISSING_STARTUP_PROBE,
            delta_class=DeltaClass.WILL_MISBEHAVE,
            confidence=0.95,
            summary=(
                "The application exposes a /ready or readiness endpoint but "
                "service.yaml does not configure a startupProbe. Cloud Run will not "
                "wait for the application to be ready before routing traffic."
            ),
            evidence=[
                Evidence(
                    source=EvidenceSource.INTENDED,
                    locator="app-code",
                    snippet="expects_startup_probe=True (/ready route detected)",
                ),
                Evidence(
                    source=EvidenceSource.DECLARED,
                    locator="service.yaml/spec.template.spec.containers",
                    snippet="startupProbe: (absent)",
                ),
            ],
            recommendation=(
                "Add a startupProbe to service.yaml:\n"
                "  startupProbe:\n"
                "    httpGet:\n"
                "      path: /ready\n"
                "      port: 8080"
            ),
        ))

    # ------------------------------------------------------------------ #
    # Rule 7: pid1-signal-unsafe [will-misbehave]
    # ------------------------------------------------------------------ #
    # Shell-form CMD wraps the process in /bin/sh -c, which becomes PID 1.
    # /bin/sh does NOT forward SIGTERM → graceful shutdown never reaches the app.
    if not intended.pid1_signal_safe:
        deltas.append(ReconciliationDelta(
            rule_id=RuleId.PID1_SIGNAL_UNSAFE,
            delta_class=DeltaClass.WILL_MISBEHAVE,
            confidence=1.0,
            summary=(
                "Dockerfile uses shell-form CMD (e.g. CMD npm start), which wraps the process "
                "in '/bin/sh -c'. The shell becomes PID 1 and does NOT forward SIGTERM to the "
                "application. Cloud Run sends SIGTERM for graceful shutdown → the app will be "
                "SIGKILL'd after the timeout, losing in-flight requests."
            ),
            evidence=[
                Evidence(
                    source=EvidenceSource.INTENDED,
                    locator="Dockerfile",
                    snippet="pid1_signal_safe=False (shell-form CMD detected)",
                ),
            ],
            recommendation=(
                "Use exec-form CMD with a JSON array. Examples:\n"
                "  CMD [\"uvicorn\", \"app:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8080\"]\n"
                "  CMD [\"/usr/local/bin/entrypoint.sh\"]\n"
                "Exec-form makes the process PID 1 directly and receives SIGTERM."
            ),
        ))

    # ------------------------------------------------------------------ #
    # Rule 8: over-broad-sa-role [will-misbehave/security]
    # ------------------------------------------------------------------ #
    # Runtime SA has roles/owner or roles/editor → full project access.
    # If credentials are compromised, blast radius = entire project.
    for role in live.sa_iam_roles:
        if role in _OVERBROAD_ROLES:
            deltas.append(ReconciliationDelta(
                rule_id=RuleId.OVER_BROAD_SA_ROLE,
                delta_class=DeltaClass.WILL_MISBEHAVE,
                confidence=1.0,
                summary=(
                    f"The runtime service account has the over-broad role '{role}' on the project. "
                    f"This violates least-privilege: if the SA credentials are compromised, "
                    f"the attacker has full project access."
                ),
                evidence=[
                    Evidence(
                        source=EvidenceSource.LIVE,
                        locator=f"projects/{live.project_id}/iam-policy",
                        snippet=f"sa_iam_roles contains {role}",
                    ),
                ],
                recommendation=(
                    f"Remove the '{role}' role from the runtime SA and grant only the minimum "
                    f"required roles (e.g. roles/run.invoker + roles/secretmanager.secretAccessor). "
                    f"Principle of least privilege (AI Operating Principles §4 analog)."
                ),
            ))

    # ------------------------------------------------------------------ #
    # Rule 9: missing-required-role [will-fail]
    # ------------------------------------------------------------------ #
    # An API in intended.required_apis implies a service role that the SA doesn't have.
    current_roles = set(live.sa_iam_roles)
    for api in intended.required_apis:
        required_role = _API_TO_REQUIRED_ROLE.get(api)
        if required_role and required_role not in current_roles:
            deltas.append(ReconciliationDelta(
                rule_id=RuleId.MISSING_REQUIRED_ROLE,
                delta_class=DeltaClass.WILL_FAIL,
                confidence=0.90,
                summary=(
                    f"The code requires API '{api}' (detected in intended contract) but the "
                    f"runtime SA does not have the expected role '{required_role}'. "
                    f"API calls from the running service will fail with PERMISSION_DENIED."
                ),
                evidence=[
                    Evidence(
                        source=EvidenceSource.INTENDED,
                        locator="app-code",
                        snippet=f"required_apis=['{api}']",
                    ),
                    Evidence(
                        source=EvidenceSource.LIVE,
                        locator=f"projects/{live.project_id}/iam-policy",
                        snippet=f"sa_iam_roles does not contain {required_role}",
                    ),
                ],
                recommendation=(
                    f"Grant '{required_role}' to the runtime SA:\n"
                    f"  gcloud projects add-iam-policy-binding <PROJECT_ID> \\\n"
                    f"    --member='serviceAccount:<SA_EMAIL>' \\\n"
                    f"    --role='{required_role}'"
                ),
            ))

    # ------------------------------------------------------------------ #
    # Rule 10: api-not-enabled [will-fail]
    # ------------------------------------------------------------------ #
    # A required API is not enabled in the project → API calls fail immediately.
    enabled_apis_set = set(live.enabled_apis)
    all_required_apis: set[str] = set(intended.required_apis)

    for api in sorted(all_required_apis):
        if api not in enabled_apis_set:
            deltas.append(ReconciliationDelta(
                rule_id=RuleId.API_NOT_ENABLED,
                delta_class=DeltaClass.WILL_FAIL,
                confidence=0.90,
                summary=(
                    f"API '{api}' is required by the application but is NOT enabled "
                    f"in project '{live.project_id}'. All calls to this API will fail with "
                    f"'API not enabled' error."
                ),
                evidence=[
                    Evidence(
                        source=EvidenceSource.INTENDED,
                        locator="app-code",
                        snippet=f"required_apis=['{api}']",
                    ),
                    Evidence(
                        source=EvidenceSource.LIVE,
                        locator=f"projects/{live.project_id}/enabled-apis",
                        snippet=f"{api}: not enabled",
                    ),
                ],
                recommendation=(
                    f"Enable the API:\n"
                    f"  gcloud services enable {api} --project={live.project_id}"
                ),
            ))

    # ------------------------------------------------------------------ #
    # Rule 11: scaling-cost-flag [cost-risk, advisory]
    # ------------------------------------------------------------------ #
    scaling = declared.scaling
    scaling_issues: list[str] = []

    if scaling.max_scale is not None and scaling.concurrency is not None:
        total_capacity = scaling.max_scale * scaling.concurrency
        if total_capacity > _SCALING_HIGH_CONCURRENCY_THRESHOLD:
            scaling_issues.append(
                f"maxScale={scaling.max_scale} x concurrency={scaling.concurrency} "
                f"= {total_capacity} total concurrent requests (threshold: "
                f"{_SCALING_HIGH_CONCURRENCY_THRESHOLD})"
            )

    if scaling.min_scale is not None and scaling.min_scale >= _SCALING_HIGH_MIN_SCALE:
        scaling_issues.append(
            f"minScale={scaling.min_scale} keeps {scaling.min_scale} instances always running"
        )

    if scaling.cpu_throttling is False:
        # cpu_throttling=False means CPU is NOT throttled (always allocated) → cost risk
        scaling_issues.append(
            "cpu-throttling=false: CPU always allocated (not throttled when idle) → cost risk"
        )

    if scaling_issues:
        deltas.append(ReconciliationDelta(
            rule_id=RuleId.SCALING_COST_FLAG,
            delta_class=DeltaClass.COST_RISK,
            confidence=0.85,
            summary=(
                "Scaling configuration may result in unexpectedly high costs: "
                + "; ".join(scaling_issues)
            ),
            evidence=[
                Evidence(
                    source=EvidenceSource.DECLARED,
                    locator="service.yaml/metadata.annotations",
                    snippet=(
                        f"minScale={scaling.min_scale}, maxScale={scaling.max_scale}, "
                        f"concurrency={scaling.concurrency}, cpu_throttling={scaling.cpu_throttling}"
                    ),
                ),
            ],
            recommendation=(
                "Review scaling configuration:\n"
                "  - Set maxScale to a realistic ceiling for your traffic\n"
                "  - Use minScale=0 for dev/staging to avoid idle costs\n"
                "  - Enable cpu-throttling (true) unless you need CPU during cold start"
            ),
        ))

    # ------------------------------------------------------------------ #
    # Rule 12: unpinned-base-image [will-misbehave advisory]
    # ------------------------------------------------------------------ #
    if not intended.base_image_pinned:
        deltas.append(ReconciliationDelta(
            rule_id=RuleId.UNPINNED_BASE_IMAGE,
            delta_class=DeltaClass.WILL_MISBEHAVE,
            confidence=0.90,
            summary=(
                "The Dockerfile's base image is not pinned to a specific tag or SHA digest "
                "(uses :latest or no tag). This means builds are not reproducible: "
                "a future build may use a different base image with breaking changes."
            ),
            evidence=[
                Evidence(
                    source=EvidenceSource.INTENDED,
                    locator="Dockerfile",
                    snippet="base_image_pinned=False",
                ),
            ],
            recommendation=(
                "Pin the base image to a specific version or SHA digest. Examples:\n"
                "  FROM python:3.12-slim         (version tag — reproducible)\n"
                "  FROM python:3.12@sha256:...   (SHA — maximally reproducible)\n"
                "Avoid 'FROM python:latest' or 'FROM python' (no tag)."
            ),
        ))

    # ------------------------------------------------------------------ #
    # LLM-03 seam (DEFERRED to Increment 2)
    # ------------------------------------------------------------------ #
    # TODO (LLM-03, Increment 2): After deterministic rules, pass inputs to Gemini 2.5 Pro
    # for ambiguity classification:
    #
    #   ambiguous_inputs = {
    #       "intended": intended.to_dict(),
    #       "declared": declared.to_dict(),
    #       "live": live.to_dict(),
    #       "existing_deltas": [d.to_dict() for d in deltas],
    #   }
    #   # Redact before model call (AI Operating Principles §3)
    #   from launchguard.guardrails.redact import redact
    #   safe_inputs = redact(ambiguous_inputs)
    #   # Gemini 2.5 Pro classifies ambiguous residue
    #   # Returns delta_class=needs-review with confidence < 1.0
    #   # AI Operating Principles §8: low confidence → needs-review, never will-fail

    return deltas


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _secret_source_note(
    secret_name: str,
    intended: IntendedContract,
    declared: DeclaredState,
) -> str:
    """Return a human-readable note about where a secret is referenced."""
    in_intended = secret_name in intended.secret_refs
    in_declared = secret_name in declared.secret_refs
    if in_intended and in_declared:
        return "in both code (Intended) and service.yaml (Declared)"
    elif in_intended:
        return "in code (Intended contract)"
    elif in_declared:
        return "in service.yaml (Declared state)"
    return "in the configuration"
