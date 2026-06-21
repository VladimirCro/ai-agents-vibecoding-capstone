"""
tests/test_gcloud_read.py — Unit tests for BE-03 gcloud_read tool.

Increment 2 (network machine):
  - live/record execution path is now implemented (subprocess.run-based).
  - The NotImplementedError test from Increment 1 has been REPLACED with a
    mock-based test that verifies the live-path behaviour without network access.
  - A skip-if-absent integration test loads the real fixture (worknote-ai.json)
    when present (CI offline → skip; CI live → passes).

Tests:
  - fixture mode returns LiveState dict
  - mutating verb raises GuardrailReadonlyViolation + logs audit trip (before exec)
  - read verbs pass the guardrail check
  - live path: correct gcloud cmd built, JSON parsed, fields mapped, non-zero exit raises
  - live path: mutating verb is still blocked BEFORE subprocess is called
  - live path: sa-iam → sa_iam_roles extracted for runtime SA only
  - live path: enabled-apis → enabled_apis list extracted
  - live path: secrets → name-only list, accessor_members=[]
  - live path: secret-accessors → accessor_members for secretAccessor role only
  - live path: run-config → minimal safe dict (port, probe bools, scaling, ref names)
  - integration (skip-if-absent): real worknote-ai.json loads with expected shape + zero values
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from launchguard.guardrails.audit import get_audit_logger
from launchguard.guardrails.enforce import GuardrailReadonlyViolation
from launchguard.models import Mode
from launchguard.tools.gcloud_read import (
    _map_enabled_apis,
    _map_run_config,
    _map_sa_iam,
    _map_secret_accessors,
    _map_secrets_list,
    gcloud_read,
)

# ---------------------------------------------------------------------------
# Fixture data (synthetic — no real values)
# ---------------------------------------------------------------------------

_SA_IAM_RAW: dict[str, Any] = {
    "bindings": [
        {
            "role": "roles/run.invoker",
            "members": ["serviceAccount:sa@project.iam.gserviceaccount.com"],
        },
        {
            "role": "roles/secretmanager.secretAccessor",
            "members": ["serviceAccount:sa@project.iam.gserviceaccount.com"],
        },
        {
            "role": "roles/owner",
            "members": ["user:admin@example.com"],  # different principal — must NOT appear
        },
    ],
    "etag": "abc123",
}

_ENABLED_APIS_RAW: list[dict[str, Any]] = [
    {"config": {"name": "run.googleapis.com"}},
    {"config": {"name": "secretmanager.googleapis.com"}},
    {"config": {"name": "iam.googleapis.com"}},
]

_SECRETS_LIST_RAW: list[dict[str, Any]] = [
    {"name": "projects/123/secrets/JWT_SECRET_KEY", "createTime": "2026-01-01T00:00:00Z"},
    {"name": "projects/123/secrets/DATABASE_URL", "createTime": "2026-01-01T00:00:00Z"},
]

_SECRET_ACCESSORS_RAW: dict[str, Any] = {
    "bindings": [
        {
            "role": "roles/secretmanager.secretAccessor",
            "members": ["serviceAccount:sa@project.iam.gserviceaccount.com"],
        },
        {
            "role": "roles/secretmanager.admin",  # different role — must NOT appear
            "members": ["user:admin@example.com"],
        },
    ],
    "etag": "xyz789",
}

_RUN_CONFIG_RAW: dict[str, Any] = {
    "metadata": {
        "name": "my-service",
        "annotations": {
            "autoscaling.knative.dev/maxScale": "5",
            "run.googleapis.com/cpu-throttling": "true",
        },
    },
    "spec": {
        "template": {
            "metadata": {
                "annotations": {
                    "autoscaling.knative.dev/minScale": "1",
                },
            },
            "spec": {
                "containerConcurrency": 80,
                "containers": [
                    {
                        "image": "gcr.io/project/app:latest",
                        "ports": [{"containerPort": 8080, "name": "http1"}],
                        "env": [
                            # Plain env var — should NOT appear in secret_env_ref_names
                            {"name": "LOG_LEVEL", "value": "info"},
                            # Secret ref — name only, no value
                            {
                                "name": "JWT_SECRET_KEY",
                                "valueFrom": {
                                    "secretKeyRef": {
                                        "name": "JWT_SECRET_KEY",
                                        "key": "latest",
                                    },
                                },
                            },
                        ],
                        "livenessProbe": {"httpGet": {"path": "/health", "port": 8080}},
                        # No startupProbe — should be False
                    },
                ],
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Test fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_audit():
    logger = get_audit_logger()
    logger.reset()
    yield
    logger.reset()


# ---------------------------------------------------------------------------
# Existing tests (unchanged — still green)
# ---------------------------------------------------------------------------

class TestGcloudReadFixtureMode:
    def test_fixture_mode_returns_live_state(self):
        """fixture mode returns a LiveState dict."""
        result = gcloud_read(
            resource="secrets",
            verb="list",
            project_id="worknote-ai-like-project",
            mode=Mode.FIXTURE,
            fixture_name="worknote-ai-like",
        )
        assert "project_id" in result
        assert "secrets" in result
        assert result["mode"] == Mode.FIXTURE

    def test_mutating_verb_raises_409(self):
        """A mutating verb must raise GuardrailReadonlyViolation."""
        with pytest.raises(GuardrailReadonlyViolation) as exc_info:
            gcloud_read(
                resource="sa-iam",
                verb="add-iam-policy-binding",
                project_id="test-project",
                mode=Mode.FIXTURE,
            )
        assert exc_info.value.code == "GUARDRAIL_READONLY_VIOLATION"

    def test_mutating_verb_logs_trip(self):
        """Mutating verb must log a guardrail trip."""
        logger = get_audit_logger()
        with pytest.raises(GuardrailReadonlyViolation):
            gcloud_read(
                resource="sa-iam",
                verb="delete",
                project_id="test-project",
                mode=Mode.FIXTURE,
            )
        trips = logger.get_guardrail_trips()
        assert len(trips) == 1
        assert trips[0].code == "GUARDRAIL_READONLY_VIOLATION"
        assert trips[0].blocked is True

    def test_mutating_verb_blocked_before_exec(self):
        """Guardrail must fire BEFORE any gcloud execution (no side effects)."""
        logger = get_audit_logger()
        try:
            gcloud_read(
                resource="run-config",
                verb="set-iam-policy",
                project_id="test-project",
                mode=Mode.LIVE,  # Live mode — would execute if verb were allowed
            )
        except GuardrailReadonlyViolation:
            pass  # expected
        trips = logger.get_guardrail_trips()
        assert len(trips) == 1

    def test_read_verb_passes_guardrail(self):
        """Read verbs (describe, list, get-iam-policy) pass the guardrail."""
        try:
            gcloud_read(
                resource="secrets",
                verb="list",
                project_id="worknote-ai-like-project",
                mode=Mode.FIXTURE,
                fixture_name="worknote-ai-like",
            )
        except GuardrailReadonlyViolation:
            pytest.fail("Read verb 'list' must not raise GuardrailReadonlyViolation")

    def test_invalid_resource_raises(self):
        """An unknown resource must raise ValueError."""
        with pytest.raises(ValueError, match="Invalid resource"):
            gcloud_read(
                resource="unknown-resource",
                verb="list",
                project_id="test-project",
                mode=Mode.FIXTURE,
            )

    def test_fixture_logs_tool_call(self):
        """Successful fixture call must log a tool call."""
        logger = get_audit_logger()
        gcloud_read(
            resource="secrets",
            verb="list",
            project_id="worknote-ai-like-project",
            mode=Mode.FIXTURE,
            fixture_name="worknote-ai-like",
        )
        calls = logger.get_tool_calls()
        assert len(calls) >= 1
        assert calls[0].tool_name == "gcloud_read"
        assert calls[0].outcome == "ok"


# ---------------------------------------------------------------------------
# Mapper unit tests (pure — no network, no mock needed)
# ---------------------------------------------------------------------------

class TestMapSaIam:
    def test_extracts_roles_for_runtime_sa_only(self):
        """_map_sa_iam extracts only roles bound to the runtime SA, not other principals."""
        result = _map_sa_iam(
            _SA_IAM_RAW,
            project_id="test-project",
            runtime_sa="sa@project.iam.gserviceaccount.com",
        )
        assert result["project_id"] == "test-project"
        assert set(result["sa_iam_roles"]) == {
            "roles/run.invoker",
            "roles/secretmanager.secretAccessor",
        }
        # roles/owner belongs to a different principal — must NOT appear
        assert "roles/owner" not in result["sa_iam_roles"]

    def test_accepts_serviceaccount_prefix(self):
        """_map_sa_iam handles 'serviceAccount:' prefix on runtime_sa."""
        result = _map_sa_iam(
            _SA_IAM_RAW,
            project_id="test-project",
            runtime_sa="serviceAccount:sa@project.iam.gserviceaccount.com",
        )
        assert "roles/run.invoker" in result["sa_iam_roles"]

    def test_empty_bindings_returns_empty_roles(self):
        result = _map_sa_iam(
            {"bindings": []},
            project_id="proj",
            runtime_sa="sa@proj.iam.gserviceaccount.com",
        )
        assert result["sa_iam_roles"] == []
        assert result["project_id"] == "proj"

    def test_sa_not_in_any_binding(self):
        result = _map_sa_iam(
            _SA_IAM_RAW,
            project_id="proj",
            runtime_sa="other-sa@proj.iam.gserviceaccount.com",
        )
        assert result["sa_iam_roles"] == []


class TestMapEnabledApis:
    def test_extracts_config_name(self):
        result = _map_enabled_apis(_ENABLED_APIS_RAW)
        assert "run.googleapis.com" in result["enabled_apis"]
        assert "secretmanager.googleapis.com" in result["enabled_apis"]
        assert len(result["enabled_apis"]) == 3

    def test_sorted_determinism(self):
        result = _map_enabled_apis(_ENABLED_APIS_RAW)
        assert result["enabled_apis"] == sorted(result["enabled_apis"])

    def test_empty_list(self):
        result = _map_enabled_apis([])
        assert result["enabled_apis"] == []

    def test_missing_config_name_skipped(self):
        """Entries without config.name are silently skipped."""
        raw = [
            {"config": {"name": "run.googleapis.com"}},
            {"config": {}},  # no name
            {},              # no config
        ]
        result = _map_enabled_apis(raw)
        assert result["enabled_apis"] == ["run.googleapis.com"]


class TestMapSecretsList:
    def test_extracts_short_name(self):
        result = _map_secrets_list(_SECRETS_LIST_RAW)
        names = [s["name"] for s in result["secrets"]]
        assert "JWT_SECRET_KEY" in names
        assert "DATABASE_URL" in names

    def test_accessor_members_initialised_empty(self):
        """accessor_members is [] at list stage (filled by secret-accessors calls)."""
        result = _map_secrets_list(_SECRETS_LIST_RAW)
        for s in result["secrets"]:
            assert s["accessor_members"] == []

    def test_sorted_by_name(self):
        result = _map_secrets_list(_SECRETS_LIST_RAW)
        names = [s["name"] for s in result["secrets"]]
        assert names == sorted(names)

    def test_no_metadata_leaked(self):
        """createTime and other metadata must NOT appear in the mapped output."""
        result = _map_secrets_list(_SECRETS_LIST_RAW)
        for s in result["secrets"]:
            assert set(s.keys()) == {"name", "accessor_members"}


class TestMapSecretAccessors:
    def test_extracts_secretaccessor_members_only(self):
        result = _map_secret_accessors(_SECRET_ACCESSORS_RAW, secret_name="JWT_SECRET_KEY")
        assert result["name"] == "JWT_SECRET_KEY"
        assert "serviceAccount:sa@project.iam.gserviceaccount.com" in result["accessor_members"]
        # admin role member must NOT appear
        assert "user:admin@example.com" not in result["accessor_members"]

    def test_empty_bindings(self):
        result = _map_secret_accessors({"bindings": []}, secret_name="MISSING_SECRET")
        assert result["name"] == "MISSING_SECRET"
        assert result["accessor_members"] == []

    def test_no_secretaccessor_role(self):
        """Only roles/secretmanager.secretAccessor members are extracted."""
        raw = {
            "bindings": [
                {"role": "roles/secretmanager.admin", "members": ["user:a@b.com"]},
            ],
        }
        result = _map_secret_accessors(raw, secret_name="MY_SECRET")
        assert result["accessor_members"] == []


class TestMapRunConfig:
    def test_extracts_container_port(self):
        result = _map_run_config(_RUN_CONFIG_RAW)
        assert result["run_config"]["container_port"] == 8080

    def test_secret_env_ref_names_names_only(self):
        """Only the secretKeyRef.name fields are extracted — no values."""
        result = _map_run_config(_RUN_CONFIG_RAW)
        ref_names = result["run_config"]["secret_env_ref_names"]
        assert "JWT_SECRET_KEY" in ref_names
        # Plain env var name should NOT appear
        assert "LOG_LEVEL" not in ref_names

    def test_probe_presence_bools(self):
        result = _map_run_config(_RUN_CONFIG_RAW)
        rc = result["run_config"]
        assert rc["has_liveness_probe"] is True
        assert rc["has_startup_probe"] is False

    def test_scaling_extracted(self):
        result = _map_run_config(_RUN_CONFIG_RAW)
        scaling = result["run_config"]["scaling"]
        assert scaling["max_scale"] == 5
        assert scaling["min_scale"] == 1
        assert scaling["concurrency"] == 80
        assert scaling["cpu_throttling"] is True

    def test_no_image_or_annotation_blobs(self):
        """image, full annotations, and env VALUES must NOT appear in run_config."""
        result = _map_run_config(_RUN_CONFIG_RAW)
        rc = result["run_config"]
        # Only the explicitly whitelisted keys should be present
        allowed_keys = {
            "container_port", "secret_env_ref_names",
            "has_liveness_probe", "has_startup_probe", "scaling",
        }
        assert set(rc.keys()) == allowed_keys

    def test_empty_service(self):
        """Empty service.yaml structure returns safe None/False defaults."""
        result = _map_run_config({})
        rc = result["run_config"]
        assert rc["container_port"] is None
        assert rc["has_liveness_probe"] is False
        assert rc["has_startup_probe"] is False
        assert rc["secret_env_ref_names"] == []


# ---------------------------------------------------------------------------
# Live-path unit tests (mock subprocess.run — no real gcloud needed)
# ---------------------------------------------------------------------------

class TestLiveExecutionPath:
    """Unit tests for the live/record execution path using mocked subprocess."""

    def _make_mock_process(self, stdout: Any, returncode: int = 0) -> MagicMock:
        mock_proc = MagicMock()
        mock_proc.returncode = returncode
        mock_proc.stdout = json.dumps(stdout) if not isinstance(stdout, str) else stdout
        mock_proc.stderr = ""
        return mock_proc

    def test_live_sa_iam_calls_correct_command(self):
        """live mode for sa-iam calls 'gcloud projects get-iam-policy'."""
        mock_proc = self._make_mock_process(_SA_IAM_RAW)
        with patch("launchguard.tools.gcloud_read.subprocess.run", return_value=mock_proc) as mock_run:
            result = gcloud_read(
                resource="sa-iam",
                verb="get-iam-policy",
                project_id="test-proj",
                mode=Mode.LIVE,
                runtime_sa="sa@test-proj.iam.gserviceaccount.com",
            )
        cmd = mock_run.call_args[0][0]
        assert "projects" in cmd
        assert "get-iam-policy" in cmd
        assert "test-proj" in cmd
        assert "--format=json" in cmd
        assert "sa_iam_roles" in result

    def test_live_enabled_apis_calls_services_list(self):
        """live mode for enabled-apis calls 'gcloud services list'."""
        mock_proc = self._make_mock_process(_ENABLED_APIS_RAW)
        with patch("launchguard.tools.gcloud_read.subprocess.run", return_value=mock_proc) as mock_run:
            result = gcloud_read(
                resource="enabled-apis",
                verb="list",
                project_id="test-proj",
                mode=Mode.LIVE,
            )
        cmd = mock_run.call_args[0][0]
        assert "services" in cmd
        assert "list" in cmd
        assert "--project=test-proj" in cmd
        assert "enabled_apis" in result

    def test_live_secrets_list_calls_secrets_list(self):
        """live mode for secrets calls 'gcloud secrets list'."""
        mock_proc = self._make_mock_process(_SECRETS_LIST_RAW)
        with patch("launchguard.tools.gcloud_read.subprocess.run", return_value=mock_proc) as mock_run:
            result = gcloud_read(
                resource="secrets",
                verb="list",
                project_id="test-proj",
                mode=Mode.LIVE,
            )
        cmd = mock_run.call_args[0][0]
        assert "secrets" in cmd
        assert "list" in cmd
        assert "secrets" in result

    def test_live_secret_accessors_calls_get_iam_policy(self):
        """live mode for secret-accessors calls 'gcloud secrets get-iam-policy'."""
        mock_proc = self._make_mock_process(_SECRET_ACCESSORS_RAW)
        with patch("launchguard.tools.gcloud_read.subprocess.run", return_value=mock_proc) as mock_run:
            result = gcloud_read(
                resource="secret-accessors",
                verb="get-iam-policy",
                project_id="test-proj",
                mode=Mode.LIVE,
                secret_name="MY_SECRET",
            )
        cmd = mock_run.call_args[0][0]
        assert "get-iam-policy" in cmd
        assert "MY_SECRET" in cmd
        assert "accessor_members" in result

    def test_live_run_config_calls_services_describe(self):
        """live mode for run-config calls 'gcloud run services describe'."""
        mock_proc = self._make_mock_process(_RUN_CONFIG_RAW)
        with patch("launchguard.tools.gcloud_read.subprocess.run", return_value=mock_proc) as mock_run:
            result = gcloud_read(
                resource="run-config",
                verb="describe",
                project_id="test-proj",
                mode=Mode.LIVE,
                service_name="my-service",
                region="europe-west1",
            )
        cmd = mock_run.call_args[0][0]
        assert "run" in cmd
        assert "describe" in cmd
        assert "my-service" in cmd
        assert "--region=europe-west1" in cmd
        assert "run_config" in result

    def test_nonzero_exit_raises_runtime_error(self):
        """Non-zero gcloud exit must raise RuntimeError (not crash-dump stdout)."""
        mock_proc = self._make_mock_process("", returncode=1)
        mock_proc.stderr = "ERROR: (gcloud.services.list) permission denied"
        with patch("launchguard.tools.gcloud_read.subprocess.run", return_value=mock_proc):
            with pytest.raises(RuntimeError, match="gcloud command failed"):
                gcloud_read(
                    resource="enabled-apis",
                    verb="list",
                    project_id="test-proj",
                    mode=Mode.LIVE,
                )

    def test_mutating_verb_blocked_before_subprocess_in_live_mode(self):
        """Even in live mode, mutating verb is blocked BEFORE subprocess is called."""
        with patch("launchguard.tools.gcloud_read.subprocess.run") as mock_run:
            with pytest.raises(GuardrailReadonlyViolation):
                gcloud_read(
                    resource="sa-iam",
                    verb="add-iam-policy-binding",
                    project_id="test-proj",
                    mode=Mode.LIVE,
                )
        # subprocess.run must NEVER have been called
        mock_run.assert_not_called()

    def test_record_mode_calls_record_live_state(self, tmp_path: Path):
        """record mode invokes record_live_state (patched at the fixture_replay module)."""
        # record_live_state is imported lazily inside gcloud_read; patch it where it's defined.
        mock_proc = self._make_mock_process(_ENABLED_APIS_RAW)
        with patch("launchguard.tools.gcloud_read.subprocess.run", return_value=mock_proc):
            with patch(
                "launchguard.tools.fixture_replay.record_live_state"
            ) as mock_record:
                gcloud_read(
                    resource="enabled-apis",
                    verb="list",
                    project_id="test-proj",
                    mode=Mode.RECORD,
                    fixture_name="test-fixture",
                )
        # record_live_state must have been called exactly once with the fixture name
        mock_record.assert_called_once()
        call_args = mock_record.call_args
        assert call_args[0][0] == "test-fixture"

    def test_live_field_mapping_sa_iam_correct_roles(self):
        """End-to-end: live sa-iam returns only the SA's roles."""
        mock_proc = self._make_mock_process(_SA_IAM_RAW)
        with patch("launchguard.tools.gcloud_read.subprocess.run", return_value=mock_proc):
            result = gcloud_read(
                resource="sa-iam",
                verb="get-iam-policy",
                project_id="test-proj",
                mode=Mode.LIVE,
                runtime_sa="sa@project.iam.gserviceaccount.com",
            )
        assert set(result["sa_iam_roles"]) == {
            "roles/run.invoker",
            "roles/secretmanager.secretAccessor",
        }
        # roles/owner (different principal) must NOT appear
        assert "roles/owner" not in result["sa_iam_roles"]

    def test_live_no_value_blobs_in_run_config(self):
        """run-config response must not contain image digest or env value blobs."""
        mock_proc = self._make_mock_process(_RUN_CONFIG_RAW)
        with patch("launchguard.tools.gcloud_read.subprocess.run", return_value=mock_proc):
            result = gcloud_read(
                resource="run-config",
                verb="describe",
                project_id="test-proj",
                mode=Mode.LIVE,
                service_name="my-service",
            )
        rc = result["run_config"]
        # These must NOT be present in the narrow run_config
        assert "image" not in rc
        assert "annotations" not in rc
        # Plain env var value must NOT be in secret_env_ref_names
        assert "LOG_LEVEL" not in rc.get("secret_env_ref_names", [])


# ---------------------------------------------------------------------------
# Integration test — real fixture (skip if absent, so CI works offline)
# ---------------------------------------------------------------------------

_REAL_FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "gcp" / "worknote-ai.json"

EXPECTED_REAL_SECRET_NAMES = {
    "CLAMAV_FUNCTION_URL",
    "DATABASE_URL_APP",
    "DATABASE_URL_MIGRATION",
    "JWT_SECRET_KEY",
    "LITELLM_AZURE_API_KEY",
    "LITELLM_AZURE_ENDPOINT",
    "LITELLM_VERTEX_CREDENTIALS",
    "OPENAI_API_KEY",
    "SENTRY_DSN_BACKEND",
    "SENTRY_DSN_FRONTEND",
    "SES_SMTP_HOST",
    "SES_SMTP_PASSWORD",
    "SES_SMTP_USERNAME",
    "WORKNOTE_APP_DB_PASSWORD",
    "WORKNOTE_MIGRATION_DB_PASSWORD",
}

@pytest.mark.skipif(
    not _REAL_FIXTURE_PATH.exists(),
    reason="Real fixture fixtures/gcp/worknote-ai.json absent — CI offline mode. "
           "Run 'venv/bin/python scripts/record_fixture.py' to capture it.",
)
class TestRealWorknoteFixture:
    """Integration tests against the real, recorded worknote-ai.json fixture.

    These tests are SKIPPED when the fixture does not exist (offline CI / fresh checkout).
    They pass deterministically once the fixture is recorded.
    """

    def _load_fixture(self) -> dict:
        return json.loads(_REAL_FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_fixture_loads_and_has_expected_structure(self):
        data = self._load_fixture()
        assert data.get("project_id") == "worknote-ai"
        assert isinstance(data.get("sa_iam_roles"), list)
        assert isinstance(data.get("enabled_apis"), list)
        assert isinstance(data.get("secrets"), list)
        assert data.get("mode") == "fixture"

    def test_fixture_has_all_expected_real_secret_names(self):
        """All 15 real secrets must be present in the recorded fixture."""
        data = self._load_fixture()
        fixture_names = {s["name"] for s in data.get("secrets", [])}
        assert EXPECTED_REAL_SECRET_NAMES == fixture_names, (
            f"Missing: {EXPECTED_REAL_SECRET_NAMES - fixture_names} | "
            f"Extra: {fixture_names - EXPECTED_REAL_SECRET_NAMES}"
        )

    def test_fixture_accessor_members_are_principals_not_values(self):
        """accessor_members must contain principal strings, never secret values."""
        data = self._load_fixture()
        import re
        secret_value_patterns = [
            re.compile(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+'),  # JWT
            re.compile(r'(?i)(postgres|mysql|redis)://'),         # connection string
            re.compile(r'AIza[A-Za-z0-9_-]{35}'),               # GCP API key
            re.compile(r'[A-Za-z0-9+/]{60,}={0,2}'),            # long base64
        ]
        for secret in data.get("secrets", []):
            for member in secret.get("accessor_members", []):
                for pat in secret_value_patterns:
                    assert not pat.search(member), (
                        f"Secret value pattern found in accessor_members of {secret['name']}: {member[:40]}"
                    )

    def test_fixture_contains_zero_secret_values(self):
        """Full fixture text must contain zero secret value patterns."""
        text = _REAL_FIXTURE_PATH.read_text(encoding="utf-8")
        import re
        patterns = {
            "JWT token": re.compile(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+'),
            "connection string": re.compile(r'(?i)(postgres|mysql|redis)://\S+'),
            "GCP API key": re.compile(r'AIza[A-Za-z0-9_-]{35}'),
            "PEM key": re.compile(r'-----BEGIN'),
            "long base64": re.compile(r'[A-Za-z0-9+/]{60,}={0,2}'),
            "64+ hex": re.compile(r'\b[A-Fa-f0-9]{64,}\b'),
        }
        for name, pat in patterns.items():
            matches = pat.findall(text)
            assert not matches, f"Secret value pattern '{name}' found in fixture: {matches[:2]}"

    def test_fixture_loads_via_fixture_replay(self):
        """fixture_replay('worknote-ai') returns a valid LiveState."""
        from launchguard.tools.fixture_replay import fixture_replay  # noqa: PLC0415
        live = fixture_replay("worknote-ai")
        assert live.project_id == "worknote-ai"
        assert live.mode == Mode.FIXTURE
        assert len(live.secrets) == 15
        assert len(live.sa_iam_roles) > 0
        assert len(live.enabled_apis) > 0

    def test_fixture_sa_iam_roles_are_role_strings(self):
        """sa_iam_roles must be role strings like 'roles/...'."""
        data = self._load_fixture()
        for role in data.get("sa_iam_roles", []):
            assert isinstance(role, str)
            assert role.startswith("roles/"), f"Unexpected role format: {role}"

    def test_fixture_runtime_sa_is_principal_string(self):
        """runtime_sa must be a serviceAccount: principal string."""
        data = self._load_fixture()
        sa = data.get("runtime_sa", "")
        assert sa.startswith("serviceAccount:"), f"Unexpected runtime_sa format: {sa}"

    def test_fixture_run_config_has_expected_structure(self):
        """run_config must have the narrow safe fields only."""
        data = self._load_fixture()
        rc = data.get("run_config")
        assert rc is not None
        assert rc.get("container_port") == 8080
        assert isinstance(rc.get("has_liveness_probe"), bool)
        assert isinstance(rc.get("has_startup_probe"), bool)
        scaling = rc.get("scaling", {})
        assert "min_scale" in scaling
        assert "max_scale" in scaling
        assert "concurrency" in scaling
        # secret_env_ref_names must be a list of strings
        ref_names = rc.get("secret_env_ref_names", [])
        assert isinstance(ref_names, list)
        for name in ref_names:
            assert isinstance(name, str)
