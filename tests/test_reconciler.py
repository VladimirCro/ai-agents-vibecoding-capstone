"""
tests/test_reconciler.py — Unit tests for BE-05 Reconciler rule engine.

Tests every detector rule + the worknote-ai-like hero fixture.

Critical:
  - secret-ref-without-secretAccessor fires on worknote-ai-like (hero TP)
  - Each detector fires on crafted inputs
  - Reconciler has NO external tool imports (verified statically)
  - No model calls (verified by absence of any google/genai import)
"""

from pathlib import Path

from launchguard.models import (
    DeclaredState,
    DeltaClass,
    HostBinding,
    IntendedContract,
    LiveState,
    Mode,
    RuleId,
    ScalingConfig,
    SecretAccessorEntry,
)
from launchguard.reconciler.engine import reconcile

# ---------------------------------------------------------------------------
# Builders for minimal test fixtures
# ---------------------------------------------------------------------------

def make_intended(
    port: int | None = 8080,
    host_binding: str = HostBinding.ALL,
    pid1_signal_safe: bool = True,
    base_image_pinned: bool = True,
    non_root_user: bool = True,
    secret_refs: list[str] | None = None,
    env_vars: list[str] | None = None,
    expects_health_probe: bool = False,
    expects_startup_probe: bool = False,
    required_apis: list[str] | None = None,
    confidence: float = 1.0,
) -> IntendedContract:
    return IntendedContract(
        port=port,
        host_binding=host_binding,
        pid1_signal_safe=pid1_signal_safe,
        base_image_pinned=base_image_pinned,
        non_root_user=non_root_user,
        secret_refs=secret_refs or [],
        env_vars=env_vars or [],
        expects_health_probe=expects_health_probe,
        expects_startup_probe=expects_startup_probe,
        required_apis=required_apis or [],
        confidence=confidence,
    )


def make_declared(
    container_port: int | None = 8080,
    secret_refs: list[str] | None = None,
    env_vars: list[str] | None = None,
    has_liveness_probe: bool = True,
    has_startup_probe: bool = True,
    scaling: ScalingConfig | None = None,
    service_account: str | None = None,
) -> DeclaredState:
    return DeclaredState(
        container_port=container_port,
        secret_refs=secret_refs or [],
        env_vars=env_vars or [],
        has_liveness_probe=has_liveness_probe,
        has_startup_probe=has_startup_probe,
        scaling=scaling or ScalingConfig(min_scale=0, max_scale=3, concurrency=80),
        service_account=service_account,
    )


def make_live(
    project_id: str = "test-project",
    runtime_sa: str = "serviceAccount:sa@test.iam.gserviceaccount.com",
    sa_iam_roles: list[str] | None = None,
    enabled_apis: list[str] | None = None,
    secrets: list[SecretAccessorEntry] | None = None,
    mode: str = Mode.FIXTURE,
) -> LiveState:
    return LiveState(
        project_id=project_id,
        runtime_sa=runtime_sa,
        sa_iam_roles=sa_iam_roles or ["roles/run.invoker"],
        enabled_apis=enabled_apis or ["run.googleapis.com", "secretmanager.googleapis.com"],
        secrets=secrets or [],
        mode=mode,
    )


_DEFAULT_SA = "serviceAccount:sa@test.iam.gserviceaccount.com"


def make_secret(name: str, has_sa_access: bool, sa: str = _DEFAULT_SA) -> SecretAccessorEntry:
    return SecretAccessorEntry(
        name=name,
        accessor_members=[sa] if has_sa_access else [],
    )


def find_delta(deltas: list, rule_id: str):
    """Find first delta with given rule_id, or None."""
    for d in deltas:
        if d.rule_id == rule_id:
            return d
    return None


# ---------------------------------------------------------------------------
# Rule 1: secret-ref-without-secretAccessor [KILLER]
# ---------------------------------------------------------------------------

class TestSecretRefWithoutSecretAccessor:
    def test_fires_when_sa_missing_accessor(self):
        """KILLER: secret exists in SM but SA has no accessor → will-fail."""
        secret = make_secret("SECRET_FOO", has_sa_access=False)
        intended = make_intended(secret_refs=["SECRET_FOO"])
        declared = make_declared(secret_refs=["SECRET_FOO"])
        live = make_live(secrets=[secret])

        deltas = reconcile(intended, declared, live)
        d = find_delta(deltas, RuleId.SECRET_REF_WITHOUT_SECRET_ACCESSOR)

        assert d is not None, "KILLER delta must fire"
        assert d.delta_class == DeltaClass.WILL_FAIL
        assert d.confidence >= 0.9
        assert "SECRET_FOO" in d.summary
        assert len(d.evidence) >= 1

    def test_does_not_fire_when_sa_has_accessor(self):
        """Must NOT fire when SA already has secretAccessor."""
        secret = make_secret("SECRET_FOO", has_sa_access=True)
        intended = make_intended(secret_refs=["SECRET_FOO"])
        declared = make_declared(secret_refs=["SECRET_FOO"])
        live = make_live(secrets=[secret])

        deltas = reconcile(intended, declared, live)
        d = find_delta(deltas, RuleId.SECRET_REF_WITHOUT_SECRET_ACCESSOR)
        assert d is None, "Must NOT fire when SA has accessor"

    def test_names_secret_sa_and_role_in_summary(self):
        """Summary must name the secret, SA, and the missing role."""
        secret = make_secret("JWT_SECRET_KEY", has_sa_access=False)
        intended = make_intended(secret_refs=["JWT_SECRET_KEY"])
        declared = make_declared(secret_refs=["JWT_SECRET_KEY"])
        live = make_live(secrets=[secret])

        deltas = reconcile(intended, declared, live)
        d = find_delta(deltas, RuleId.SECRET_REF_WITHOUT_SECRET_ACCESSOR)

        assert d is not None
        assert "JWT_SECRET_KEY" in d.summary
        assert "roles/secretmanager.secretAccessor" in d.recommendation

    def test_hero_fixture_fires_on_jwt_secret_key(self):
        """Hero TP: worknote-ai-like fixture fires on JWT_SECRET_KEY."""
        from launchguard.tools.declared_parser import parse_declared_state
        from launchguard.tools.fixture_replay import fixture_replay
        from launchguard.tools.repo_tools import build_intended_contract

        repo_path = str(Path(__file__).parents[1] / "fixtures" / "repos" / "worknote-ai-like")
        service_yaml = str(Path(repo_path) / "infra" / "cloud-run" / "service.yaml")

        intended = build_intended_contract(repo_path)
        declared = parse_declared_state(service_yaml)
        live = fixture_replay("worknote-ai-like")

        deltas = reconcile(intended, declared, live)

        # Hero assertion: JWT_SECRET_KEY killer delta must fire
        killer_deltas = [
            d for d in deltas
            if d.rule_id == RuleId.SECRET_REF_WITHOUT_SECRET_ACCESSOR
            and "JWT_SECRET_KEY" in d.summary
        ]
        assert len(killer_deltas) >= 1, (
            f"Expected secret-ref-without-secretAccessor for JWT_SECRET_KEY. "
            f"Got deltas: {[(d.rule_id, d.summary[:80]) for d in deltas]}"
        )
        killer = killer_deltas[0]
        assert killer.delta_class == DeltaClass.WILL_FAIL
        print(f"\n[HERO DELTA] {killer.rule_id}: {killer.summary[:120]}")


# ---------------------------------------------------------------------------
# Rule 2: secret-declared-not-created
# ---------------------------------------------------------------------------

class TestSecretDeclaredNotCreated:
    def test_fires_when_secret_missing_from_sm(self):
        """Secret declared in service.yaml but absent from SM → will-fail."""
        declared = make_declared(secret_refs=["MISSING_SECRET"])
        intended = make_intended()
        live = make_live(secrets=[])  # No secrets in SM

        deltas = reconcile(intended, declared, live)
        d = find_delta(deltas, RuleId.SECRET_DECLARED_NOT_CREATED)

        assert d is not None
        assert d.delta_class == DeltaClass.WILL_FAIL
        assert "MISSING_SECRET" in d.summary

    def test_does_not_fire_when_secret_exists(self):
        """Must NOT fire when secret exists in SM."""
        secret = make_secret("EXISTING_SECRET", has_sa_access=True)
        declared = make_declared(secret_refs=["EXISTING_SECRET"])
        intended = make_intended()
        live = make_live(secrets=[secret])

        deltas = reconcile(intended, declared, live)
        d = find_delta(deltas, RuleId.SECRET_DECLARED_NOT_CREATED)
        assert d is None


# ---------------------------------------------------------------------------
# Rule 3: port-mismatch
# ---------------------------------------------------------------------------

class TestPortMismatch:
    def test_fires_when_ports_differ(self):
        """Port 8080 vs 3000 → will-misbehave port-mismatch."""
        intended = make_intended(port=8080)
        declared = make_declared(container_port=3000)
        live = make_live()

        deltas = reconcile(intended, declared, live)
        d = find_delta(deltas, RuleId.PORT_MISMATCH)

        assert d is not None
        assert d.delta_class == DeltaClass.WILL_MISBEHAVE
        assert d.confidence == 1.0
        assert "8080" in d.summary
        assert "3000" in d.summary

    def test_does_not_fire_when_ports_match(self):
        """Must NOT fire when intended port == declared container_port."""
        intended = make_intended(port=8080)
        declared = make_declared(container_port=8080)
        live = make_live()

        deltas = reconcile(intended, declared, live)
        d = find_delta(deltas, RuleId.PORT_MISMATCH)
        assert d is None

    def test_does_not_fire_when_port_null(self):
        """Must NOT fire when either port is None."""
        intended = make_intended(port=None)
        declared = make_declared(container_port=8080)
        live = make_live()

        deltas = reconcile(intended, declared, live)
        d = find_delta(deltas, RuleId.PORT_MISMATCH)
        assert d is None


# ---------------------------------------------------------------------------
# Rule 4: host-not-0.0.0.0
# ---------------------------------------------------------------------------

class TestHostNot0000:
    def test_fires_for_localhost(self):
        """host_binding=localhost → will-misbehave."""
        intended = make_intended(host_binding=HostBinding.LOCALHOST)
        deltas = reconcile(intended, make_declared(), make_live())
        d = find_delta(deltas, RuleId.HOST_NOT_0_0_0_0)

        assert d is not None
        assert d.delta_class == DeltaClass.WILL_MISBEHAVE
        assert d.confidence == 1.0

    def test_fires_for_127_0_0_1(self):
        """host_binding=127.0.0.1 → will-misbehave."""
        intended = make_intended(host_binding=HostBinding.LOOPBACK)
        deltas = reconcile(intended, make_declared(), make_live())
        d = find_delta(deltas, RuleId.HOST_NOT_0_0_0_0)
        assert d is not None

    def test_does_not_fire_for_0_0_0_0(self):
        """host_binding=0.0.0.0 must NOT fire."""
        intended = make_intended(host_binding=HostBinding.ALL)
        deltas = reconcile(intended, make_declared(), make_live())
        d = find_delta(deltas, RuleId.HOST_NOT_0_0_0_0)
        assert d is None

    def test_does_not_fire_for_unknown(self):
        """host_binding=unknown must NOT fire (ambiguous, handled by LLM-03)."""
        intended = make_intended(host_binding=HostBinding.UNKNOWN)
        deltas = reconcile(intended, make_declared(), make_live())
        d = find_delta(deltas, RuleId.HOST_NOT_0_0_0_0)
        assert d is None


# ---------------------------------------------------------------------------
# Rule 5 & 6: missing-health-probe / missing-startup-probe
# ---------------------------------------------------------------------------

class TestMissingProbes:
    def test_missing_health_probe_fires(self):
        """expects_health_probe=True + no livenessProbe → will-misbehave."""
        intended = make_intended(expects_health_probe=True)
        declared = make_declared(has_liveness_probe=False, has_startup_probe=True)
        deltas = reconcile(intended, declared, make_live())
        d = find_delta(deltas, RuleId.MISSING_HEALTH_PROBE)
        assert d is not None
        assert d.delta_class == DeltaClass.WILL_MISBEHAVE

    def test_missing_startup_probe_fires(self):
        """expects_startup_probe=True + no startupProbe → will-misbehave."""
        intended = make_intended(expects_startup_probe=True)
        declared = make_declared(has_liveness_probe=True, has_startup_probe=False)
        deltas = reconcile(intended, declared, make_live())
        d = find_delta(deltas, RuleId.MISSING_STARTUP_PROBE)
        assert d is not None

    def test_no_fire_when_probe_present(self):
        """Must NOT fire when the probe is configured in service.yaml."""
        intended = make_intended(expects_health_probe=True, expects_startup_probe=True)
        declared = make_declared(has_liveness_probe=True, has_startup_probe=True)
        deltas = reconcile(intended, declared, make_live())
        assert find_delta(deltas, RuleId.MISSING_HEALTH_PROBE) is None
        assert find_delta(deltas, RuleId.MISSING_STARTUP_PROBE) is None

    def test_no_fire_when_app_does_not_expect_probe(self):
        """Must NOT fire when app doesn't expose a /health route."""
        intended = make_intended(expects_health_probe=False)
        declared = make_declared(has_liveness_probe=False)
        deltas = reconcile(intended, declared, make_live())
        assert find_delta(deltas, RuleId.MISSING_HEALTH_PROBE) is None


# ---------------------------------------------------------------------------
# Rule 7: pid1-signal-unsafe
# ---------------------------------------------------------------------------

class TestPid1SignalUnsafe:
    def test_fires_for_shell_form_cmd(self):
        """pid1_signal_safe=False → will-misbehave."""
        intended = make_intended(pid1_signal_safe=False)
        deltas = reconcile(intended, make_declared(), make_live())
        d = find_delta(deltas, RuleId.PID1_SIGNAL_UNSAFE)

        assert d is not None
        assert d.delta_class == DeltaClass.WILL_MISBEHAVE
        assert d.confidence == 1.0

    def test_does_not_fire_for_exec_form(self):
        """pid1_signal_safe=True must NOT fire."""
        intended = make_intended(pid1_signal_safe=True)
        deltas = reconcile(intended, make_declared(), make_live())
        d = find_delta(deltas, RuleId.PID1_SIGNAL_UNSAFE)
        assert d is None


# ---------------------------------------------------------------------------
# Rule 8: over-broad-sa-role
# ---------------------------------------------------------------------------

class TestOverBroadSaRole:
    def test_fires_for_owner(self):
        """roles/owner → will-misbehave over-broad-sa-role."""
        live = make_live(sa_iam_roles=["roles/owner", "roles/run.invoker"])
        deltas = reconcile(make_intended(), make_declared(), live)
        d = find_delta(deltas, RuleId.OVER_BROAD_SA_ROLE)

        assert d is not None
        assert d.delta_class == DeltaClass.WILL_MISBEHAVE
        assert "roles/owner" in d.summary

    def test_fires_for_editor(self):
        """roles/editor → over-broad-sa-role."""
        live = make_live(sa_iam_roles=["roles/editor"])
        deltas = reconcile(make_intended(), make_declared(), live)
        d = find_delta(deltas, RuleId.OVER_BROAD_SA_ROLE)
        assert d is not None

    def test_does_not_fire_for_specific_roles(self):
        """Minimal roles must NOT fire."""
        live = make_live(sa_iam_roles=["roles/run.invoker", "roles/secretmanager.secretAccessor"])
        deltas = reconcile(make_intended(), make_declared(), live)
        d = find_delta(deltas, RuleId.OVER_BROAD_SA_ROLE)
        assert d is None


# ---------------------------------------------------------------------------
# Rule 9: missing-required-role
# ---------------------------------------------------------------------------

class TestMissingRequiredRole:
    def test_fires_when_api_role_missing(self):
        """aiplatform.googleapis.com in required_apis but SA lacks roles/aiplatform.user."""
        intended = make_intended(required_apis=["aiplatform.googleapis.com"])
        live = make_live(
            sa_iam_roles=["roles/run.invoker"],
            enabled_apis=["run.googleapis.com", "aiplatform.googleapis.com"],
        )
        deltas = reconcile(intended, make_declared(), live)
        d = find_delta(deltas, RuleId.MISSING_REQUIRED_ROLE)
        assert d is not None
        assert d.delta_class == DeltaClass.WILL_FAIL

    def test_does_not_fire_when_role_present(self):
        """Must NOT fire when SA has the required role."""
        intended = make_intended(required_apis=["aiplatform.googleapis.com"])
        live = make_live(
            sa_iam_roles=["roles/run.invoker", "roles/aiplatform.user"],
            enabled_apis=["aiplatform.googleapis.com"],
        )
        deltas = reconcile(intended, make_declared(), live)
        d = find_delta(deltas, RuleId.MISSING_REQUIRED_ROLE)
        assert d is None


# ---------------------------------------------------------------------------
# Rule 10: api-not-enabled
# ---------------------------------------------------------------------------

class TestApiNotEnabled:
    def test_fires_when_api_not_enabled(self):
        """Required API not in enabled_apis → will-fail."""
        intended = make_intended(required_apis=["aiplatform.googleapis.com"])
        live = make_live(
            enabled_apis=["run.googleapis.com"],  # aiplatform missing
            sa_iam_roles=["roles/aiplatform.user"],
        )
        deltas = reconcile(intended, make_declared(), live)
        d = find_delta(deltas, RuleId.API_NOT_ENABLED)
        assert d is not None
        assert d.delta_class == DeltaClass.WILL_FAIL
        assert "aiplatform.googleapis.com" in d.summary

    def test_does_not_fire_when_api_enabled(self):
        """Must NOT fire when API is in enabled_apis."""
        intended = make_intended(required_apis=["aiplatform.googleapis.com"])
        live = make_live(enabled_apis=["run.googleapis.com", "aiplatform.googleapis.com"])
        deltas = reconcile(intended, make_declared(), live)
        d = find_delta(deltas, RuleId.API_NOT_ENABLED)
        assert d is None


# ---------------------------------------------------------------------------
# Rule 11: scaling-cost-flag
# ---------------------------------------------------------------------------

class TestScalingCostFlag:
    def test_fires_for_high_concurrency(self):
        """High max_scale * concurrency → cost-risk."""
        scaling = ScalingConfig(min_scale=0, max_scale=10, concurrency=80)  # 800 total
        declared = make_declared(scaling=scaling)
        deltas = reconcile(make_intended(), declared, make_live())
        d = find_delta(deltas, RuleId.SCALING_COST_FLAG)
        assert d is not None
        assert d.delta_class == DeltaClass.COST_RISK

    def test_fires_for_cpu_throttling_false(self):
        """cpu_throttling=False → cost-risk."""
        scaling = ScalingConfig(min_scale=0, max_scale=3, concurrency=10, cpu_throttling=False)
        declared = make_declared(scaling=scaling)
        deltas = reconcile(make_intended(), declared, make_live())
        d = find_delta(deltas, RuleId.SCALING_COST_FLAG)
        assert d is not None

    def test_does_not_fire_for_reasonable_scaling(self):
        """Low concurrency + CPU throttling → no cost-risk flag."""
        scaling = ScalingConfig(min_scale=0, max_scale=3, concurrency=10, cpu_throttling=True)
        declared = make_declared(scaling=scaling)
        deltas = reconcile(make_intended(), declared, make_live())
        d = find_delta(deltas, RuleId.SCALING_COST_FLAG)
        assert d is None


# ---------------------------------------------------------------------------
# Rule 12: unpinned-base-image
# ---------------------------------------------------------------------------

class TestUnpinnedBaseImage:
    def test_fires_for_unpinned(self):
        """base_image_pinned=False → will-misbehave advisory."""
        intended = make_intended(base_image_pinned=False)
        deltas = reconcile(intended, make_declared(), make_live())
        d = find_delta(deltas, RuleId.UNPINNED_BASE_IMAGE)
        assert d is not None
        assert d.delta_class == DeltaClass.WILL_MISBEHAVE

    def test_does_not_fire_for_pinned(self):
        """base_image_pinned=True must NOT fire."""
        intended = make_intended(base_image_pinned=True)
        deltas = reconcile(intended, make_declared(), make_live())
        d = find_delta(deltas, RuleId.UNPINNED_BASE_IMAGE)
        assert d is None


# ---------------------------------------------------------------------------
# Structural guarantees
# ---------------------------------------------------------------------------

class TestReconcilerStructuralGuarantees:
    def test_every_delta_has_at_least_one_evidence(self):
        """Every delta must have at least one evidence entry (contract minItems=1)."""
        # Trigger multiple rules
        intended = make_intended(
            port=8080,
            host_binding=HostBinding.LOCALHOST,
            pid1_signal_safe=False,
            base_image_pinned=False,
        )
        declared = make_declared(container_port=3000)
        live = make_live(sa_iam_roles=["roles/owner"])

        deltas = reconcile(intended, declared, live)
        for d in deltas:
            assert len(d.evidence) >= 1, (
                f"Delta {d.rule_id} has no evidence entries"
            )

    def test_every_delta_has_valid_rule_id(self):
        """Every delta.rule_id must match api-contracts.yaml enum."""
        from launchguard.models import RuleId
        valid_ids = {
            RuleId.SECRET_REF_WITHOUT_SECRET_ACCESSOR,
            RuleId.SECRET_DECLARED_NOT_CREATED,
            RuleId.PORT_MISMATCH,
            RuleId.HOST_NOT_0_0_0_0,
            RuleId.MISSING_HEALTH_PROBE,
            RuleId.MISSING_STARTUP_PROBE,
            RuleId.PID1_SIGNAL_UNSAFE,
            RuleId.OVER_BROAD_SA_ROLE,
            RuleId.MISSING_REQUIRED_ROLE,
            RuleId.API_NOT_ENABLED,
            RuleId.SCALING_COST_FLAG,
            RuleId.UNPINNED_BASE_IMAGE,
            RuleId.AMBIGUOUS,
        }
        intended = make_intended(pid1_signal_safe=False, base_image_pinned=False)
        deltas = reconcile(intended, make_declared(), make_live())
        for d in deltas:
            assert d.rule_id in valid_ids, f"Unknown rule_id: {d.rule_id}"

    def test_high_confidence_for_deterministic_rules(self):
        """Deterministic rules must have confidence >= 0.85."""
        intended = make_intended(port=8080, pid1_signal_safe=False)
        declared = make_declared(container_port=3000)
        live = make_live(sa_iam_roles=["roles/owner"])
        deltas = reconcile(intended, declared, live)
        for d in deltas:
            assert d.confidence >= 0.85, (
                f"Delta {d.rule_id} has confidence {d.confidence} < 0.85"
            )

    def test_no_model_import_in_engine(self):
        """Reconciler engine must not import google/genai (no model calls)."""
        import launchguard.reconciler.engine as engine_module
        # Check the module's namespace for any google-related imports
        engine_source = Path(engine_module.__file__).read_text()
        assert "google" not in engine_source.lower() or "google" not in [
            line.strip()[:10] for line in engine_source.splitlines()
            if line.strip().startswith("import") or line.strip().startswith("from")
        ], "Engine must not import google (no model calls)"
        # Check directly
        assert "from google" not in engine_source
        assert "import google" not in engine_source
        assert "genai" not in engine_source

    def test_reconciler_has_no_external_tool_imports(self):
        """Reconciler engine must not import any tool modules."""
        engine_source = Path(
            Path(__file__).parents[1] / "launchguard" / "reconciler" / "engine.py"
        ).read_text()
        assert "from launchguard.tools" not in engine_source
        assert "import launchguard.tools" not in engine_source
        assert "gcloud_read" not in engine_source
        assert "fixture_replay" not in engine_source
        assert "read_file" not in engine_source

    def test_clean_input_produces_zero_deltas(self):
        """Perfect configuration → no deltas (READY state)."""
        intended = make_intended(
            port=8080,
            host_binding=HostBinding.ALL,
            pid1_signal_safe=True,
            base_image_pinned=True,
            non_root_user=True,
            secret_refs=["MY_SECRET"],
            expects_health_probe=False,
            expects_startup_probe=False,
            required_apis=[],
        )
        declared = make_declared(
            container_port=8080,
            secret_refs=["MY_SECRET"],
            has_liveness_probe=True,
            has_startup_probe=True,
            scaling=ScalingConfig(min_scale=0, max_scale=3, concurrency=10, cpu_throttling=True),
        )
        live = make_live(
            sa_iam_roles=["roles/run.invoker", "roles/secretmanager.secretAccessor"],
            secrets=[make_secret("MY_SECRET", has_sa_access=True)],
            enabled_apis=["run.googleapis.com", "secretmanager.googleapis.com"],
        )
        deltas = reconcile(intended, declared, live)
        assert deltas == [], f"Expected no deltas but got: {[(d.rule_id, d.summary[:60]) for d in deltas]}"
