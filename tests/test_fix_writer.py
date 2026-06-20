"""
tests/test_fix_writer.py — BE-06 + LLM-04: FixWriter tools + pipeline.

Covers:
  - propose_patch produces a concrete fix per rule_id (and the KILLER add-iam-policy-binding)
  - propose_patch.applied is ALWAYS False (AI Operating Principles §2)
  - open_pr mock dry-run renders a preview + returns merged=False
  - open_pr against a default branch trips GUARDRAIL_PR_TARGET_VIOLATION (409)
  - open_pr real mode without a token raises a clear deferral (no half-created PR)
  - run_fix_writer end-to-end: verdict BLOCKED on the killer, scorecard JSON+MD render
  - No secret VALUE leaks into any patch/PR body (names only, §3)

All offline. No google-adk, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from launchguard.fix_writer_core import (
    render_scorecard_json,
    render_scorecard_markdown,
    run_fix_writer,
    write_scorecard,
)
from launchguard.guardrails.audit import get_audit_logger
from launchguard.models import (
    DeltaClass,
    Evidence,
    EvidenceSource,
    PatchKind,
    ReconciliationDelta,
    RuleId,
    Verdict,
)
from launchguard.reconciler.engine import reconcile
from launchguard.tools.declared_parser import parse_declared_state
from launchguard.tools.fix_tools import (
    GuardrailPrTargetViolation,
    open_pr,
    propose_patch,
)
from launchguard.tools.fixture_replay import fixture_replay
from launchguard.tools.repo_tools import build_intended_contract

_REPO_ROOT = Path(__file__).resolve().parents[1]
_HERO = _REPO_ROOT / "fixtures" / "repos" / "worknote-ai-like"
_HERO_YAML = _HERO / "infra" / "cloud-run" / "service.yaml"


@pytest.fixture(autouse=True)
def _reset_audit():
    get_audit_logger().reset()
    yield
    get_audit_logger().reset()


def _killer_delta() -> ReconciliationDelta:
    return ReconciliationDelta(
        rule_id=RuleId.SECRET_REF_WITHOUT_SECRET_ACCESSOR,
        delta_class=DeltaClass.WILL_FAIL,
        confidence=0.98,
        summary="Secret 'JWT_SECRET_KEY' is referenced but the runtime SA lacks secretAccessor.",
        evidence=[
            Evidence(EvidenceSource.LIVE, "secretmanager/JWT_SECRET_KEY/iam-policy", "accessor_members=[]"),
            Evidence(EvidenceSource.DECLARED, "service.yaml/env", "secretKeyRef.name=JWT_SECRET_KEY"),
        ],
    )


class TestProposePatch:
    def test_killer_produces_exact_iam_binding_command(self):
        patch = propose_patch(
            _killer_delta(),
            context={"project_id": "my-proj", "runtime_sa": "serviceAccount:sa@my-proj.iam.gserviceaccount.com"},
        )
        assert patch.kind == PatchKind.GCLOUD_COMMAND
        assert "gcloud secrets add-iam-policy-binding JWT_SECRET_KEY" in patch.content
        assert "roles/secretmanager.secretAccessor" in patch.content
        assert "--project=my-proj" in patch.content
        assert "sa@my-proj.iam.gserviceaccount.com" in patch.content

    def test_applied_always_false(self):
        patch = propose_patch(_killer_delta())
        assert patch.applied is False
        # even round-tripping through dict cannot flip it
        assert patch.to_dict()["applied"] is False

    @pytest.mark.parametrize(
        "rule_id,expected_kind_in",
        [
            (RuleId.SECRET_DECLARED_NOT_CREATED, {PatchKind.GCLOUD_COMMAND}),
            (RuleId.PORT_MISMATCH, {PatchKind.SERVICE_YAML_DIFF}),
            (RuleId.MISSING_HEALTH_PROBE, {PatchKind.SERVICE_YAML_DIFF}),
            (RuleId.MISSING_STARTUP_PROBE, {PatchKind.SERVICE_YAML_DIFF}),
            (RuleId.PID1_SIGNAL_UNSAFE, {PatchKind.DOCKERFILE_DIFF}),
            (RuleId.OVER_BROAD_SA_ROLE, {PatchKind.GCLOUD_COMMAND}),
            (RuleId.MISSING_REQUIRED_ROLE, {PatchKind.GCLOUD_COMMAND}),
            (RuleId.API_NOT_ENABLED, {PatchKind.GCLOUD_COMMAND}),
            (RuleId.SCALING_COST_FLAG, {PatchKind.SERVICE_YAML_DIFF}),
            (RuleId.UNPINNED_BASE_IMAGE, {PatchKind.DOCKERFILE_DIFF}),
            (RuleId.HOST_NOT_0_0_0_0, {PatchKind.DOCKERFILE_DIFF}),
        ],
    )
    def test_every_rule_has_a_concrete_patch(self, rule_id, expected_kind_in):
        delta = ReconciliationDelta(
            rule_id=rule_id, delta_class=DeltaClass.WILL_MISBEHAVE, confidence=0.9,
            summary=f"test {rule_id}", evidence=[Evidence(EvidenceSource.INTENDED, "x", "y")],
        )
        patch = propose_patch(delta)
        assert patch.rule_id == rule_id
        assert patch.kind in expected_kind_in
        assert patch.content.strip(), "patch content must be non-empty"
        assert patch.applied is False

    def test_needs_review_gets_nondestructive_note(self):
        delta = ReconciliationDelta(
            rule_id=RuleId.AMBIGUOUS, delta_class=DeltaClass.NEEDS_REVIEW, confidence=0.5,
            summary="ambiguous thing", evidence=[Evidence(EvidenceSource.INTENDED, "x", "y")],
        )
        patch = propose_patch(delta)
        assert "NEEDS REVIEW" in patch.content
        # must NOT contain an executable mutation command
        assert "add-iam-policy-binding" not in patch.content


class TestOpenPr:
    def test_mock_dry_run_renders_preview_and_returns_unmerged(self, tmp_path, monkeypatch):
        patch = propose_patch(_killer_delta())
        result = open_pr(
            repo="example/repo", branch="launchguard/fix-x",
            title="t", body="b", patches=[patch], mock=True,
        )
        assert result["merged"] is False
        assert result["pr_url"].startswith("file://")
        # the preview file exists
        preview = _REPO_ROOT / "eval" / "pr_preview" / "launchguard_fix-x.md"
        assert preview.exists()
        assert "DRY-RUN" in preview.read_text()

    @pytest.mark.parametrize("branch", ["main", "master", "develop", "trunk"])
    def test_default_branch_is_blocked(self, branch):
        with pytest.raises(GuardrailPrTargetViolation) as exc:
            open_pr("example/repo", branch, "t", "b", [], mock=True)
        assert exc.value.code == "GUARDRAIL_PR_TARGET_VIOLATION"
        # the trip is logged
        trips = get_audit_logger().get_guardrail_trips()
        assert any(t.code == "GUARDRAIL_PR_TARGET_VIOLATION" for t in trips)

    def test_real_mode_without_token_raises_clear_deferral(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        with pytest.raises(RuntimeError, match="DEFERRED"):
            open_pr("example/repo", "launchguard/fix-y", "t", "b", [], mock=False)

    def test_body_redaction_is_line_bounded(self):
        """
        Regression: the shared redactor's greedy keyword=VALUE regex must not span lines and
        eat adjacent Markdown. A body whose evidence line ends in '...name=JWT_SECRET_KEY'
        followed by a '**Proposed fix**' header must keep that header intact.
        """
        body = (
            "- `declared` @ `env` — secretKeyRef.name=JWT_SECRET_KEY\n"
            "\n"
            "**Proposed fix** (gcloud-command):\n"
        )
        result = open_pr("example/repo", "launchguard/fix-z", "title", body, [], mock=True)
        preview = Path(result["pr_url"].replace("file://", "").replace("%20", " "))
        text = preview.read_text()
        assert "**Proposed fix**" in text, "Markdown header was corrupted by redaction"


class TestRunFixWriter:
    def test_end_to_end_blocked_on_hero(self):
        intended = build_intended_contract(str(_HERO))
        declared = parse_declared_state(str(_HERO_YAML))
        live = fixture_replay("worknote-ai-like")
        deltas = reconcile(intended, declared, live)
        result = run_fix_writer(
            deltas,
            context={"project_id": live.project_id, "runtime_sa": live.runtime_sa},
            repo="example/worknote-ai", branch="launchguard/fix-readiness", open_pr_mock=True,
        )
        assert result.scorecard.verdict == Verdict.BLOCKED
        assert result.scorecard.counts.will_fail >= 1
        assert result.pr_url is not None
        # the killer patch is present and names the secret
        killer = next(
            (p for p in result.patches if p.rule_id == RuleId.SECRET_REF_WITHOUT_SECRET_ACCESSOR),
            None,
        )
        assert killer is not None
        assert "JWT_SECRET_KEY" in killer.content
        assert all(p.applied is False for p in result.patches)

    def test_pr_body_grounded_and_redacted(self):
        intended = build_intended_contract(str(_HERO))
        declared = parse_declared_state(str(_HERO_YAML))
        live = fixture_replay("worknote-ai-like")
        deltas = reconcile(intended, declared, live)
        result = run_fix_writer(deltas, context={"project_id": live.project_id, "runtime_sa": live.runtime_sa})
        body = result.pr_body_markdown
        # grounded: references the rule_id + evidence
        assert "secret-ref-without-secretAccessor" in body
        assert "Evidence" in body
        # redaction discipline: no obvious secret-value markers
        assert "BEGIN PRIVATE KEY" not in body

    def test_scorecard_json_md_render_and_write(self, tmp_path):
        deltas = [_killer_delta()]
        result = run_fix_writer(deltas)
        sc_json = render_scorecard_json(result.scorecard)
        assert sc_json["verdict"] == "BLOCKED"
        assert "will_fail" in sc_json["counts"]
        md = render_scorecard_markdown(result.scorecard)
        assert "BLOCKED" in md
        json_path, md_path = write_scorecard(result.scorecard, out_dir=tmp_path, stem="t")
        assert json_path.exists() and md_path.exists()
        loaded = json.loads(json_path.read_text())
        assert loaded["verdict"] == "BLOCKED"
