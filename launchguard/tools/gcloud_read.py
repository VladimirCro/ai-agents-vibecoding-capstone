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
  fixture — delegates to fixture_replay (zero network); used in Increment 1 tests
  live    — builds and executes the gcloud shell command (live GCP access required)
  record  — like live but also saves the redacted snapshot to fixtures/gcp/

In Increment 1:
  fixture mode is fully implemented and tested.
  live/record mode: the command STRING is built (and guardrail-checked) but NOT executed
  because Increment 1 has no live GCP access.  The build path is implemented so that
  Increment 2 can flip the mode without code changes.

Resources supported:
  sa-iam          → gcloud projects get-iam-policy <project>
  enabled-apis    → gcloud services list --project <project>
  secrets         → gcloud secrets list --project <project>
  secret-accessors → gcloud secrets get-iam-policy <secret> --project <project>
  run-config      → gcloud run services describe <service> --project <project>
"""

from __future__ import annotations

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

    Returns:
        dict — LiveStateFragment (partial LiveState fields).

    Raises:
        GuardrailReadonlyViolation: if verb is not in GCLOUD_READ_VERBS (pre-exec, 409).
        ValueError: if resource or required parameters are invalid.
        FileNotFoundError: if mode=fixture and fixture file not found.
    """
    logger = get_audit_logger()

    # GUARDRAIL CHECK — must be first, before any other logic
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

        # Increment 1: DO NOT EXECUTE — no live GCP access in Increment 1.
        # The command is built and guardrail-checked; live execution is Increment 2.
        # In Increment 2, remove this block and uncomment the subprocess.run() below.
        #
        # TODO (Increment 2): Execute live gcloud call:
        #   result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        #   if result.returncode != 0:
        #       raise RuntimeError(f"gcloud failed: {result.stderr}")
        #   raw = json.loads(result.stdout)
        #   redacted = redact_snapshot(raw)
        #   if mode == Mode.RECORD:
        #       from launchguard.tools.fixture_replay import record_live_state
        #       record_live_state(fixture_name or project_id, redacted)
        #   return redacted

        raise NotImplementedError(
            f"gcloud_read mode='{mode}' not available in Increment 1 (no live GCP access). "
            f"Use mode='fixture' with a pre-recorded fixture. "
            f"Command that WOULD run (read-only, guardrail-verified): {' '.join(cmd)}"
        )

    else:
        raise ValueError(f"Invalid mode '{mode}'. Must be one of: live, record, fixture.")
