"""
launchguard.tools.fixture_replay — BE-04: Golden-JSON fixture layer.

Tools / functions:
    fixture_replay(fixture_name) -> LiveState
        Load a pre-recorded, redacted LiveState from fixtures/gcp/<name>.json.
        Zero network. Deterministic: replay twice → byte-identical output.

    record_live_state(fixture_name, raw_data) -> None
        Redact a raw LiveState dict and persist to fixtures/gcp/<name>.json.
        Called in record mode by gcloud_read (Increment 2 live capture path).

    redact_snapshot(raw) -> dict
        Re-exported from guardrails.redact for the fixture capture path.

AI Operating Principles:
    §6 Determinism: same fixture → same LiveState every time (keys sorted, no randomness)
    §3 Redaction: fixtures are redacted AT CAPTURE TIME — no secret values ever stored

Fixture format (fixtures/gcp/<name>.json):
    {
        "project_id": "my-project",
        "runtime_sa": "sa@project.iam.gserviceaccount.com",
        "sa_iam_roles": ["roles/run.invoker", ...],
        "enabled_apis": ["run.googleapis.com", ...],
        "secrets": [
            {"name": "JWT_SECRET_KEY", "accessor_members": []},
            {"name": "OTHER_SECRET", "accessor_members": ["serviceAccount:sa@..."]}
        ],
        "mode": "fixture",
        "run_config": null
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from launchguard.guardrails.redact import redact_snapshot as _redact_snapshot
from launchguard.models import LiveState, Mode, SecretAccessorEntry

# ---------------------------------------------------------------------------
# Fixture directory resolution
# ---------------------------------------------------------------------------

def _fixtures_dir() -> Path:
    """
    Return the fixtures/gcp/ directory path.

    Resolves relative to the repo root (two levels up from this file's location:
    launchguard/tools/fixture_replay.py → launchguard/ → repo_root/).
    """
    return Path(__file__).resolve().parents[2] / "fixtures" / "gcp"


# ---------------------------------------------------------------------------
# fixture_replay — zero-network LiveState loader
# ---------------------------------------------------------------------------

def fixture_replay(fixture_name: str) -> LiveState:
    """
    Load a golden-JSON LiveState snapshot from fixtures/gcp/<fixture_name>.json.

    Deterministic: given the same fixture_name, always returns the same LiveState.
    The JSON file is loaded with sorted keys during serialization so that
    round-trip to_dict() → json.dumps(sort_keys=True) is byte-identical.

    Args:
        fixture_name: Name without .json extension (e.g. "worknote-ai-like").

    Returns:
        LiveState parsed from the fixture file.

    Raises:
        FileNotFoundError: if fixtures/gcp/<fixture_name>.json does not exist.
            Maps to contract 404 (fixture not found).
    """
    fixture_path = _fixtures_dir() / f"{fixture_name}.json"

    if not fixture_path.exists():
        raise FileNotFoundError(
            f"Fixture not found: {fixture_path}. "
            f"Available fixtures: {_list_available_fixtures()}. "
            f"Run in record mode to capture a new fixture, or create one manually."
        )

    raw = json.loads(fixture_path.read_text(encoding="utf-8"))

    # Validate and parse into LiveState
    live_state = _parse_live_state(raw, mode=Mode.FIXTURE)
    return live_state


def _parse_live_state(raw: dict[str, Any], mode: str = Mode.FIXTURE) -> LiveState:
    """Parse a raw dict into a LiveState, overriding mode."""
    secrets = [
        SecretAccessorEntry(
            name=s["name"],
            accessor_members=s.get("accessor_members", []),
        )
        for s in raw.get("secrets", [])
    ]
    return LiveState(
        project_id=raw.get("project_id", ""),
        runtime_sa=raw.get("runtime_sa"),
        sa_iam_roles=raw.get("sa_iam_roles", []),
        enabled_apis=raw.get("enabled_apis", []),
        secrets=secrets,
        mode=mode,
        run_config=raw.get("run_config"),
    )


def _list_available_fixtures() -> list[str]:
    """Return names of available fixture files (without .json extension)."""
    fixtures_dir = _fixtures_dir()
    if not fixtures_dir.exists():
        return []
    return [f.stem for f in fixtures_dir.glob("*.json")]


# ---------------------------------------------------------------------------
# record_live_state — capture-time redaction + persistence
# ---------------------------------------------------------------------------

def record_live_state(fixture_name: str, raw_data: dict[str, Any]) -> None:
    """
    Redact a raw GCP LiveState snapshot and persist it to fixtures/gcp/.

    Called in record mode by gcloud_read (Increment 2).  The raw_data comes from
    live gcloud calls and MAY contain sensitive values — redact_snapshot() removes them
    before the file is written.

    AI Operating Principles §3 + §6:
      Redaction happens at capture time so the stored fixture is safe to commit.
      The fixture is JSON with sorted keys for deterministic byte output.

    Args:
        fixture_name: Name without .json extension (e.g. "my-project").
        raw_data:     Raw LiveState dict from gcloud (may contain secret values).

    Side effects:
        Writes fixtures/gcp/<fixture_name>.json (creates dir if needed).
        Never writes secret VALUES — redact_snapshot() masks them first.
    """
    # Step 1: Redact at capture time (MANDATORY — AI Operating Principles §3)
    redacted = _redact_snapshot(raw_data)

    # Step 2: Ensure fixture directory exists
    fixtures_dir = _fixtures_dir()
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    # Step 3: Write with sorted keys for deterministic output (AI Operating Principles §6)
    fixture_path = fixtures_dir / f"{fixture_name}.json"
    fixture_path.write_text(
        json.dumps(redacted, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# redact_snapshot — re-exported for the fixture capture seam
# ---------------------------------------------------------------------------

def redact_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Redact a raw GCP snapshot before persisting to fixtures/gcp/.

    Re-exports launchguard.guardrails.redact.redact_snapshot() at the fixture
    layer so that callers can do a capture-time redaction pass without importing
    the guardrails module directly.

    Strips:
      - Secret values (API keys, tokens, connection strings)
      - JWT / base64-encoded credential blobs
      - Keeps: secret NAMES, accessor_members, roles, API names, project IDs

    Args:
        raw: Raw dict from gcloud (may contain sensitive values).

    Returns:
        Redacted dict safe for storage in fixtures/ directory.
    """
    return _redact_snapshot(raw)


# ---------------------------------------------------------------------------
# Determinism verification helper (used in tests)
# ---------------------------------------------------------------------------

def replay_to_json(fixture_name: str) -> str:
    """
    Load fixture and serialize to JSON with sorted keys.

    Used by tests to verify byte-identical output across two replay calls.
    fixture_replay(x) called twice must produce json.dumps(..., sort_keys=True)
    that is identical.

    Args:
        fixture_name: Fixture name (without .json).

    Returns:
        JSON string with sorted keys.
    """
    state = fixture_replay(fixture_name)
    return json.dumps(state.to_dict(), sort_keys=True, indent=2, ensure_ascii=False)
