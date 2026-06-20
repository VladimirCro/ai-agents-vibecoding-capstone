"""
launchguard.tools.declared_parser — BE-02: Declared parser for Cloud Run service.yaml.

Tool:
    parse_declared_state(service_yaml_path, cloudbuild_path=None) -> DeclaredState

Parses a Cloud Run service.yaml (Knative serving spec) into DeclaredState.

Key behaviors:
  - container_port: from spec.template.spec.containers[0].ports[0].containerPort
  - secret_refs: all env valueFrom.secretKeyRef.name values (and volume secretRefs)
  - env_vars: plain (non-secret) env var names
  - has_liveness_probe / has_startup_probe: from livenessProbe / startupProbe presence
  - service_account: from spec.template.spec.serviceAccountName
  - scaling: from run.googleapis.com/minScale, /maxScale annotations + containerConcurrency
  - templated_unresolved: any field value matching ${VAR} or $VAR is flagged

${ENV} substitution handling:
  Cloud Run service.yaml files often use ${ENV} placeholders that are resolved at
  deploy time by Cloud Build or a deploy script.  We do NOT crash on these; instead
  we record them in templated_unresolved and use None / safe defaults where needed.

Uses pyyaml (already installed in venv).

Acceptance:
  Given worknote-ai service.yaml → container_port=8080, all 9 secretKeyRef names,
  scaling.min/max/concurrency populated, ${ENV} placeholders → no crash + marked.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from launchguard.models import DeclaredState, ScalingConfig

# ---------------------------------------------------------------------------
# Template placeholder detection
# ---------------------------------------------------------------------------

_TEMPLATE_RE = re.compile(r"\$\{[A-Z_][A-Z0-9_]*\}|\$[A-Z_][A-Z0-9_]+")


def _is_templated(value: Any) -> bool:
    """Return True if value is a string containing a ${VAR} or $VAR placeholder."""
    if not isinstance(value, str):
        return False
    return bool(_TEMPLATE_RE.search(value))


def _collect_unresolved(value: Any, path: str, unresolved: list[str]) -> None:
    """Recursively collect template placeholders found in YAML values."""
    if isinstance(value, str) and _is_templated(value):
        unresolved.append(f"{path}={value}")
    elif isinstance(value, dict):
        for k, v in value.items():
            _collect_unresolved(v, f"{path}.{k}", unresolved)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _collect_unresolved(item, f"{path}[{i}]", unresolved)


def _safe_int(value: Any, path: str, unresolved: list[str]) -> int | None:
    """
    Convert value to int; return None if it is a template placeholder or non-integer.

    Appends to unresolved if the value is a placeholder.
    """
    if _is_templated(str(value) if value is not None else ""):
        unresolved.append(f"{path}={value}")
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_str(value: Any) -> str | None:
    """Return value as str if it is a non-placeholder string, else None."""
    if value is None:
        return None
    if _is_templated(str(value)):
        return None  # caller handles unresolved tracking
    return str(value)


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_declared_state(
    service_yaml_path: str,
    cloudbuild_path: str | None = None,  # noqa: ARG001 (reserved for Increment 2)
) -> DeclaredState:
    """
    Parse a Cloud Run service.yaml into DeclaredState.

    Handles the worknote-ai pattern:
      - Heavily templated (${ENV} placeholders throughout)
      - containerPort 8080 in spec.template.spec.containers[0].ports[0].containerPort
      - 9 secretKeyRef names in env.valueFrom.secretKeyRef
      - livenessProbe / startupProbe at spec.template.spec.containers[0].livenessProbe
      - serviceAccountName templated (${SA_EMAIL})
      - Autoscaling annotations: run.googleapis.com/minScale, maxScale
      - containerConcurrency in spec.template.spec.containerConcurrency
      - cpu-throttling annotation: run.googleapis.com/cpu-throttling

    Args:
        service_yaml_path: Absolute path to service.yaml.
        cloudbuild_path:   Optional path to cloudbuild.yaml (reserved; not used in Increment 1).

    Returns:
        DeclaredState with all parsed fields. Templated values → templated_unresolved.

    Raises:
        FileNotFoundError: if service_yaml_path does not exist.
        yaml.YAMLError: if the file is not valid YAML.
    """
    path = Path(service_yaml_path)
    if not path.exists():
        raise FileNotFoundError(f"service.yaml not found: {service_yaml_path}")

    content = path.read_text(encoding="utf-8")
    doc = yaml.safe_load(content)

    if not isinstance(doc, dict):
        raise ValueError(f"Expected YAML mapping at root of {service_yaml_path}")

    unresolved: list[str] = []

    # ------------------------------------------------------------------ #
    # Navigate to the container spec
    # ------------------------------------------------------------------ #
    spec = doc.get("spec", {}) or {}
    template = spec.get("template", {}) or {}
    template_spec = template.get("spec", {}) or {}
    template_metadata = template.get("metadata", {}) or {}
    template_annotations = template_metadata.get("annotations", {}) or {}
    # Also check top-level metadata annotations
    top_metadata = doc.get("metadata", {}) or {}
    top_annotations = top_metadata.get("annotations", {}) or {}

    containers: list[dict[str, Any]] = template_spec.get("containers", []) or []
    container = containers[0] if containers else {}

    # ------------------------------------------------------------------ #
    # Service name (from metadata.name)
    # ------------------------------------------------------------------ #
    raw_name = top_metadata.get("name", "")
    service_name = "" if _is_templated(str(raw_name)) else str(raw_name)
    if _is_templated(str(raw_name)):
        unresolved.append(f"metadata.name={raw_name}")

    # ------------------------------------------------------------------ #
    # container_port
    # ------------------------------------------------------------------ #
    ports: list[dict[str, Any]] = container.get("ports", []) or []
    container_port: int | None = None
    for port_entry in ports:
        raw_port = port_entry.get("containerPort")
        if raw_port is not None:
            container_port = _safe_int(raw_port, "ports[0].containerPort", unresolved)
            break

    # ------------------------------------------------------------------ #
    # env vars — split into secret_refs and plain env_vars
    # ------------------------------------------------------------------ #
    secret_refs: list[str] = []
    env_var_names: list[str] = []
    env_entries: list[dict[str, Any]] = container.get("env", []) or []

    for env_entry in env_entries:
        var_name = env_entry.get("name", "")
        value_from = env_entry.get("valueFrom", {}) or {}
        secret_key_ref = value_from.get("secretKeyRef", {}) or {}

        if secret_key_ref:
            # Secret reference — extract the secret name
            secret_name = secret_key_ref.get("name", "")
            if secret_name and not _is_templated(secret_name):
                if secret_name not in secret_refs:
                    secret_refs.append(secret_name)
            elif _is_templated(secret_name):
                unresolved.append(f"env[{var_name}].valueFrom.secretKeyRef.name={secret_name}")
        else:
            # Plain env var — record name only (not value)
            if var_name:
                raw_value = env_entry.get("value", "")
                if _is_templated(str(raw_value)):
                    unresolved.append(f"env[{var_name}].value={raw_value}")
                env_var_names.append(var_name)

    # Also check volume mounts for secret volumes
    volumes: list[dict[str, Any]] = template_spec.get("volumes", []) or []
    for vol in volumes:
        secret_vol = vol.get("secret", {}) or {}
        secret_name = secret_vol.get("secretName", "")
        if secret_name and not _is_templated(secret_name):
            if secret_name not in secret_refs:
                secret_refs.append(secret_name)

    # ------------------------------------------------------------------ #
    # Probes
    # ------------------------------------------------------------------ #
    has_liveness_probe = "livenessProbe" in container
    has_startup_probe = "startupProbe" in container

    # ------------------------------------------------------------------ #
    # Service account
    # ------------------------------------------------------------------ #
    raw_sa = template_spec.get("serviceAccountName")
    if raw_sa and _is_templated(str(raw_sa)):
        unresolved.append(f"spec.template.spec.serviceAccountName={raw_sa}")
        service_account = None
    else:
        service_account = _safe_str(raw_sa)

    # ------------------------------------------------------------------ #
    # Scaling — from annotations
    # ------------------------------------------------------------------ #
    # Annotations may be on spec.template.metadata or top-level metadata
    merged_annotations: dict[str, Any] = {}
    merged_annotations.update(top_annotations)
    merged_annotations.update(template_annotations)

    min_scale = _safe_int(
        merged_annotations.get("run.googleapis.com/minScale"),
        "annotations.run.googleapis.com/minScale",
        unresolved,
    )
    max_scale = _safe_int(
        merged_annotations.get("run.googleapis.com/maxScale"),
        "annotations.run.googleapis.com/maxScale",
        unresolved,
    )

    concurrency_raw = template_spec.get("containerConcurrency")
    concurrency = _safe_int(concurrency_raw, "spec.template.spec.containerConcurrency", unresolved)

    # cpu-throttling annotation: string "true"/"false"
    cpu_throttling_raw = merged_annotations.get("run.googleapis.com/cpu-throttling")
    if cpu_throttling_raw is None:
        cpu_throttling: bool | None = None
    elif _is_templated(str(cpu_throttling_raw)):
        unresolved.append(f"annotations.run.googleapis.com/cpu-throttling={cpu_throttling_raw}")
        cpu_throttling = None
    else:
        cpu_throttling = str(cpu_throttling_raw).lower() == "true"

    scaling = ScalingConfig(
        min_scale=min_scale,
        max_scale=max_scale,
        concurrency=concurrency,
        cpu_throttling=cpu_throttling,
    )

    # ------------------------------------------------------------------ #
    # Collect any remaining top-level template placeholders
    # ------------------------------------------------------------------ #
    _collect_unresolved(doc, "root", unresolved)
    # Deduplicate unresolved list while preserving order
    seen: set[str] = set()
    deduped_unresolved: list[str] = []
    for item in unresolved:
        if item not in seen:
            seen.add(item)
            deduped_unresolved.append(item)

    return DeclaredState(
        service_name=service_name,
        container_port=container_port,
        secret_refs=secret_refs,
        env_vars=env_var_names,
        has_liveness_probe=has_liveness_probe,
        has_startup_probe=has_startup_probe,
        service_account=service_account,
        scaling=scaling,
        templated_unresolved=deduped_unresolved,
    )
