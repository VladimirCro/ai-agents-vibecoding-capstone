"""
launchguard.tools.gcloud_read — BE-03: GcpStateInspector read-only gcloud wrapper.

Tool:
    gcloud_read(resource, verb, project_id, mode="fixture") -> dict (LiveStateFragment)

Read-only GCP state access with hard guardrail enforcement.

AI Operating Principles §1 (Read-only on cloud):
  - verb MUST be in GCLOUD_READ_VERBS before any execution
  - Mutating verb → raises GuardrailReadonlyViolation + logs audit trip (pre-exec)
  - NEVER returns secret values — only names + accessor membership

Modes:
  fixture — delegates to fixture_replay (zero network); default, used in tests
  live    — builds and executes the gcloud shell command (live GCP access required)
  record  — like live but also saves the redacted snapshot to fixtures/gcp/

Resources supported:
  sa-iam           → gcloud projects get-iam-policy <project>
  enabled-apis     → gcloud services list --project <project>
  secrets          → gcloud secrets list --project <project>
  secret-accessors → gcloud secrets get-iam-policy <secret> --project <project>
  run-config       → gcloud run services describe <service> --project <project>

Return contract (LiveStateFragment per resource):
  sa-iam:          {"sa_iam_roles": [...], "project_id": str}
  enabled-apis:    {"enabled_apis": [...]}
  secrets:         {"secrets": [{"name": str, "accessor_members": []}, ...]}
  secret-accessors: {"name": str, "accessor_members": [...]}
  run-config:      {"run_config": {"container_port": int, "secret_env_ref_names": [...],
                     "has_liveness_probe": bool, "has_startup_probe": bool,
                     "scaling": {"min_scale": ..., "max_scale": ...,
                                 "concurrency": ..., "cpu_throttling": ...}}}

REDACTION NOTE (two-layer defense, AI Operating Principles §3):
  Primary:   Each _map_*() function field-picks ONLY safe fields — no raw gcloud JSON
             passthrough.  Secret values, annotation values, image digests for env
             VALUE fields, and other blobs are never included in the output dict.
  Secondary: record_live_state() runs redact_snapshot() on the assembled LiveState dict
             before writing to disk.  This is a belt-and-suspenders second pass.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from launchguard.guardrails.audit import get_audit_logger
from launchguard.guardrails.enforce import check_gcloud_verb
from launchguard.models import Mode

# ---------------------------------------------------------------------------
# Resource → gcloud command builders
# ---------------------------------------------------------------------------

def _build_gcloud_command(
    resource: str,
    verb: str,
    project_id: str,
    service_name: str | None = None,
    secret_name: str | None = None,
    region: str = "us-central1",
) -> list[str]:
    """
    Build the gcloud command list for a given resource and verb.

    This is the COMMAND BUILDER only — it does not execute anything.
    The verb has already been guardrail-checked by the caller.

    Returns a list suitable for subprocess.run().
    """
    if resource == "sa-iam":
        return [
            "gcloud", "projects", "get-iam-policy", project_id,
            "--format=json",
        ]
    elif resource == "enabled-apis":
        return [
            "gcloud", "services", "list",
            f"--project={project_id}",
            "--format=json",
        ]
    elif resource == "secrets":
        return [
            "gcloud", "secrets", "list",
            f"--project={project_id}",
            "--format=json",
        ]
    elif resource == "secret-accessors":
        if not secret_name:
            raise ValueError("secret_name required for resource=secret-accessors")
        return [
            "gcloud", "secrets", "get-iam-policy", secret_name,
            f"--project={project_id}",
            "--format=json",
        ]
    elif resource == "run-config":
        if not service_name:
            raise ValueError("service_name required for resource=run-config")
        return [
            "gcloud", "run", "services", "describe", service_name,
            f"--project={project_id}",
            f"--region={region}",
            "--format=json",
        ]
    else:
        raise ValueError(f"Unknown resource: {resource!r}")


# ---------------------------------------------------------------------------
# Raw gcloud JSON → narrow LiveStateFragment mappers
# ---------------------------------------------------------------------------
# Each mapper field-picks ONLY safe fields — no value blobs, no secrets.
# PRIMARY redaction layer (AI Operating Principles §3).

def _map_sa_iam(raw: dict[str, Any], project_id: str, runtime_sa: str) -> dict[str, Any]:
    """
    Map gcloud projects get-iam-policy output → {"sa_iam_roles": [...], "project_id": str}.

    Extracts only the roles bound to the runtime SA member.
    The `member` string itself is a principal identifier, never a secret value.

    Args:
        raw:        Raw JSON from 'gcloud projects get-iam-policy <project> --format=json'.
        project_id: GCP project ID (echoed back for bookkeeping).
        runtime_sa: Runtime SA email (e.g. "worknote-staging-run@project.iam.gserviceaccount.com").

    Returns:
        {"sa_iam_roles": [str, ...], "project_id": str}
    """
    # Normalise: the member string in gcloud IAM bindings uses "serviceAccount:" prefix.
    # Accept either just the email or the full "serviceAccount:email" form.
    if runtime_sa.startswith("serviceAccount:"):
        sa_member = runtime_sa
    else:
        sa_member = f"serviceAccount:{runtime_sa}"

    roles: list[str] = []
    for binding in raw.get("bindings", []):
        role = binding.get("role", "")
        members = binding.get("members", [])
        if sa_member in members:
            roles.append(role)

    return {"sa_iam_roles": sorted(roles), "project_id": project_id}


def _map_enabled_apis(raw: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Map gcloud services list output → {"enabled_apis": [str, ...]}.

    Extracts only the API hostname (e.g. "run.googleapis.com") from the
    config.name field of each enabled service.  Metadata is dropped.

    Args:
        raw: Raw JSON list from 'gcloud services list --format=json'.

    Returns:
        {"enabled_apis": [str, ...]} — sorted list of API hostnames.
    """
    api_names: list[str] = []
    for service in raw:
        # config.name is the canonical API hostname like "run.googleapis.com"
        config = service.get("config", {}) or {}
        name = config.get("name", "")
        if name:
            api_names.append(name)

    return {"enabled_apis": sorted(api_names)}


def _map_secrets_list(raw: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Map gcloud secrets list output → {"secrets": [{"name": str, "accessor_members": []}, ...]}.

    Extracts only the short secret name (e.g. "JWT_SECRET_KEY") from the
    fully-qualified name path (e.g. "projects/123/secrets/JWT_SECRET_KEY").
    accessor_members is initialised to [] here — filled by secret-accessors calls.

    Args:
        raw: Raw JSON list from 'gcloud secrets list --format=json'.

    Returns:
        {"secrets": [{"name": str, "accessor_members": []}, ...]}
    """
    secrets: list[dict[str, Any]] = []
    for secret in raw:
        full_name = secret.get("name", "")
        # name is "projects/<number>/secrets/<SECRET_NAME>" — extract last segment
        short_name = full_name.split("/")[-1] if "/" in full_name else full_name
        if short_name:
            secrets.append({"name": short_name, "accessor_members": []})

    # Sort by name for determinism
    secrets.sort(key=lambda s: s["name"])
    return {"secrets": secrets}


def _map_secret_accessors(raw: dict[str, Any], secret_name: str) -> dict[str, Any]:
    """
    Map gcloud secrets get-iam-policy output → {"name": str, "accessor_members": [str, ...]}.

    Extracts only the principals holding roles/secretmanager.secretAccessor.
    Principal strings are membership identifiers (serviceAccount:..., user:..., etc.),
    never secret values.

    Args:
        raw:         Raw JSON from 'gcloud secrets get-iam-policy <secret> --format=json'.
        secret_name: Short secret name (e.g. "JWT_SECRET_KEY") — echoed back.

    Returns:
        {"name": str, "accessor_members": [str, ...]}
    """
    accessor_members: list[str] = []
    for binding in raw.get("bindings", []):
        role = binding.get("role", "")
        if role == "roles/secretmanager.secretAccessor":
            members = binding.get("members", [])
            accessor_members.extend(members)

    return {"name": secret_name, "accessor_members": sorted(accessor_members)}


def _map_run_config(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Map gcloud run services describe output → {"run_config": {...}}.

    Extracts ONLY non-sensitive structural information:
      - container_port (int | None)
      - secret_env_ref_names (list[str]) — names of secrets referenced as env vars
      - has_liveness_probe (bool)
      - has_startup_probe (bool)
      - scaling: {min_scale, max_scale, concurrency, cpu_throttling}

    NEVER includes: env VALUE fields, image digest, annotations beyond scaling,
    VPC config, or any other blob that might contain sensitive data.

    Args:
        raw: Raw JSON from 'gcloud run services describe <service> --format=json'.

    Returns:
        {"run_config": {...}}
    """
    spec = raw.get("spec", {}) or {}
    template = spec.get("template", {}) or {}
    tspec = template.get("spec", {}) or {}
    containers: list[dict[str, Any]] = tspec.get("containers", []) or []
    container: dict[str, Any] = containers[0] if containers else {}

    # template metadata annotations (scaling lives here in Knative spec)
    tmeta = template.get("metadata", {}) or {}
    t_annots: dict[str, Any] = tmeta.get("annotations", {}) or {}
    # top-level metadata annotations (Cloud Run sometimes puts scaling here)
    top_meta = raw.get("metadata", {}) or {}
    top_annots: dict[str, Any] = top_meta.get("annotations", {}) or {}
    # merge: template-level wins over top-level
    merged_annots: dict[str, Any] = {}
    merged_annots.update(top_annots)
    merged_annots.update(t_annots)

    # container_port
    ports: list[dict[str, Any]] = container.get("ports", []) or []
    container_port: int | None = None
    if ports:
        raw_port = ports[0].get("containerPort")
        if raw_port is not None:
            try:
                container_port = int(raw_port)
            except (TypeError, ValueError):
                container_port = None

    # secret env ref NAMES only (not values)
    secret_env_ref_names: list[str] = []
    for env_entry in container.get("env", []):
        value_from = env_entry.get("valueFrom", {}) or {}
        skr = value_from.get("secretKeyRef", {}) or {}
        sname = skr.get("name")
        if sname:
            secret_env_ref_names.append(sname)

    # probes — boolean presence only
    has_liveness_probe: bool = "livenessProbe" in container
    has_startup_probe: bool = "startupProbe" in container

    # scaling — pick specific annotation keys only
    def _safe_int(val: Any) -> int | None:
        if val is None:
            return None
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    def _safe_bool_str(val: Any) -> bool | None:
        if val is None:
            return None
        return str(val).lower() == "true"

    # Cloud Run uses both autoscaling.knative.dev/* and run.googleapis.com/* namespaces
    min_scale = _safe_int(
        merged_annots.get("autoscaling.knative.dev/minScale")
        or merged_annots.get("run.googleapis.com/minScale")
    )
    max_scale = _safe_int(
        merged_annots.get("autoscaling.knative.dev/maxScale")
        or merged_annots.get("run.googleapis.com/maxScale")
    )
    concurrency = _safe_int(tspec.get("containerConcurrency"))
    cpu_throttling = _safe_bool_str(merged_annots.get("run.googleapis.com/cpu-throttling"))

    run_config: dict[str, Any] = {
        "container_port": container_port,
        "secret_env_ref_names": sorted(secret_env_ref_names),
        "has_liveness_probe": has_liveness_probe,
        "has_startup_probe": has_startup_probe,
        "scaling": {
            "min_scale": min_scale,
            "max_scale": max_scale,
            "concurrency": concurrency,
            "cpu_throttling": cpu_throttling,
        },
    }

    return {"run_config": run_config}


# ---------------------------------------------------------------------------
# Live execution helper
# ---------------------------------------------------------------------------

def _execute_gcloud(cmd: list[str]) -> Any:
    """
    Execute a gcloud command and return the parsed JSON output.

    All commands are read-only (guardrail already verified by caller).
    On non-zero exit, raises RuntimeError with stderr terse message.
    stderr from read-only gcloud verbs does not contain secret values.

    Args:
        cmd: Command list built by _build_gcloud_command (read-only, guardrail-checked).

    Returns:
        Parsed JSON (dict or list depending on command).

    Raises:
        RuntimeError: if gcloud exits non-zero (includes stderr for diagnostics).
        json.JSONDecodeError: if stdout is not valid JSON.
    """
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)  # noqa: S603
    if result.returncode != 0:
        # stderr from read-only gcloud verbs is safe (auth errors, API errors)
        # Truncate to avoid including any unexpected lengthy output
        stderr_short = (result.stderr or "").strip()[:500]
        raise RuntimeError(
            f"gcloud command failed (exit {result.returncode}): {stderr_short}"
        )
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Main tool function
# ---------------------------------------------------------------------------

def gcloud_read(
    resource: str,
    verb: str,
    project_id: str,
    mode: str = Mode.FIXTURE,
    fixture_name: str | None = None,
    service_name: str | None = None,
    secret_name: str | None = None,
    region: str = "us-central1",
    runtime_sa: str = "",
) -> dict[str, Any]:
    """
    Read GCP state for a given resource (read-only, verb allow-list enforced).

    Guardrail: verb MUST be in GCLOUD_READ_VERBS.  Any mutating verb raises
    GuardrailReadonlyViolation BEFORE any execution or network call.

    Args:
        resource:     One of: sa-iam, enabled-apis, secrets, secret-accessors, run-config.
        verb:         gcloud verb — MUST be in {describe, list, get-iam-policy}.
        project_id:   GCP project ID.
        mode:         "fixture" (default), "live", or "record".
        fixture_name: Name of fixture file (without .json) for fixture mode.
        service_name: Cloud Run service name (required for resource=run-config).
        secret_name:  Secret name (required for resource=secret-accessors).
        region:       GCP region for Cloud Run (default us-central1).
        runtime_sa:   Runtime service account email (required for sa-iam mapping in
                      live/record mode; used to filter IAM bindings to this SA only).

    Returns:
        dict — LiveStateFragment (partial LiveState fields appropriate to resource).

    Raises:
        GuardrailReadonlyViolation: if verb is not in GCLOUD_READ_VERBS (pre-exec, 409).
        ValueError: if resource or required parameters are invalid.
        FileNotFoundError: if mode=fixture and fixture file not found.
        RuntimeError: if mode=live/record and gcloud exits non-zero.
    """
    logger = get_audit_logger()

    # GUARDRAIL CHECK — must be first, before any other logic (AI Operating Principles §1)
    check_gcloud_verb(verb, agent_name="GcpStateInspector")

    # Log the tool call (after guardrail passes)
    logger.log_tool_call(
        agent_name="GcpStateInspector",
        tool_name="gcloud_read",
        input_summary={
            "resource": resource,
            "verb": verb,
            "project_id": project_id,
            "mode": mode,
        },
        outcome="ok",
    )

    valid_resources = {"sa-iam", "enabled-apis", "secrets", "secret-accessors", "run-config"}
    if resource not in valid_resources:
        raise ValueError(f"Invalid resource '{resource}'. Must be one of: {sorted(valid_resources)}")

    if mode == Mode.FIXTURE:
        # Fixture mode: zero network, load from fixtures/gcp/
        from launchguard.tools.fixture_replay import fixture_replay  # noqa: PLC0415
        name = fixture_name or project_id
        live_state = fixture_replay(name)
        return live_state.to_dict()

    elif mode in (Mode.LIVE, Mode.RECORD):
        # Build the command (guardrail already checked the verb above)
        cmd = _build_gcloud_command(
            resource=resource,
            verb=verb,
            project_id=project_id,
            service_name=service_name,
            secret_name=secret_name,
            region=region,
        )

        # Execute live gcloud call (read-only, guardrail-verified above)
        raw = _execute_gcloud(cmd)

        # Map raw JSON → narrow LiveStateFragment (PRIMARY redaction — no value blobs)
        if resource == "sa-iam":
            fragment = _map_sa_iam(raw, project_id=project_id, runtime_sa=runtime_sa)
        elif resource == "enabled-apis":
            fragment = _map_enabled_apis(raw)
        elif resource == "secrets":
            fragment = _map_secrets_list(raw)
        elif resource == "secret-accessors":
            # secret_name is validated by _build_gcloud_command (raises ValueError if absent)
            fragment = _map_secret_accessors(raw, secret_name=secret_name or "")
        elif resource == "run-config":
            fragment = _map_run_config(raw)
        else:
            # Unreachable: valid_resources guard above covers all cases
            raise ValueError(f"Unknown resource: {resource!r}")  # pragma: no cover

        # Record mode: persist redacted snapshot (SECONDARY redaction layer)
        if mode == Mode.RECORD:
            from launchguard.tools.fixture_replay import record_live_state  # noqa: PLC0415
            record_live_state(fixture_name or project_id, fragment)

        return fragment

    else:
        raise ValueError(f"Invalid mode '{mode}'. Must be one of: live, record, fixture.")
