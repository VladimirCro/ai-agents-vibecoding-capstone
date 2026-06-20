"""
launchguard.fix_writer_core — BE-06 deterministic FixWriter pipeline (no ADK/model needed).

This is the testable, import-safe core that the ADK FixWriter sub-agent (sub_agents/fix_writer.py)
delegates to. It consumes classified ReconciliationDelta objects and produces:

  - a ReadinessScorecard (verdict + counts)  [api-contracts.yaml]
  - a list of ProposedPatch (one per actionable delta)
  - a rendered PR body (Markdown, redacted)
  - a readiness scorecard rendered to JSON + Markdown on disk (the headline artifact)
  - (optionally) a mock/dry-run PR via open_pr

Separation of concerns:
  - propose_patch / open_pr  → launchguard/tools/fix_tools.py  (the tools, BE-06)
  - verdict + counts          → launchguard/models.py (ReadinessScorecard.from_deltas)
  - this module               → orchestration + rendering (BE-06 "build scorecard JSON+MD")
  - PR-body narrative polish   → LLM-04 wraps render_pr_body() output (deferred, seam below)

Zero external deps (stdlib + launchguard internals). Always importable in CI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from launchguard.models import (
    ReadinessScorecard,
    ReconciliationDelta,
    Verdict,
)
from launchguard.tools.fix_tools import open_pr, propose_patch


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class FixWriterResult:
    """Everything FixWriter produces for one reconciliation run."""
    scorecard: ReadinessScorecard
    patches: list[Any]                  # list[ProposedPatch]
    pr_body_markdown: str
    pr_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scorecard": self.scorecard.to_dict(),
            "patches": [p.to_dict() for p in self.patches],
            "pr_body_markdown": self.pr_body_markdown,
            "pr_url": self.pr_url,
        }


# ---------------------------------------------------------------------------
# Verdict → human framing
# ---------------------------------------------------------------------------

_VERDICT_HEADLINE = {
    Verdict.BLOCKED: "BLOCKED — do not deploy until the will-fail findings are resolved.",
    Verdict.WARN: "WARN — deployable, but misbehave/cost risks should be reviewed.",
    Verdict.READY: "READY — no blocking or misbehave findings detected.",
}


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def run_fix_writer(
    deltas: list[ReconciliationDelta] | list[dict[str, Any]],
    *,
    context: dict[str, Any] | None = None,
    repo: str | None = None,
    branch: str = "launchguard/fix-readiness",
    open_pr_mock: bool | None = None,
) -> FixWriterResult:
    """
    Run the deterministic FixWriter pipeline over classified deltas.

    Args:
        deltas:  ReconciliationDelta objects (or dicts) from the Reconciler.
        context: project_id / runtime_sa / service_name for concrete commands.
        repo:    "owner/name" — if provided, a mock/dry-run PR is opened (offline).
        branch:  non-default PR branch (default a launchguard/* branch).
        open_pr_mock: force mock (True), real (False), or env-resolved (None).

    Returns:
        FixWriterResult (scorecard, patches, pr_body, pr_url).
    """
    norm_deltas = [
        d if isinstance(d, ReconciliationDelta) else ReconciliationDelta.from_dict(d)
        for d in deltas
    ]

    scorecard = ReadinessScorecard.from_deltas(norm_deltas)

    # Propose a patch for every actionable delta (needs-review gets an explanatory note).
    patches = [propose_patch(d, context=context) for d in norm_deltas]

    pr_body = render_pr_body(scorecard, patches, context=context)

    pr_url: str | None = None
    if repo:
        result = open_pr(
            repo=repo,
            branch=branch,
            title=_pr_title(scorecard),
            body=pr_body,
            patches=patches,
            mock=open_pr_mock,
        )
        pr_url = result["pr_url"]
        scorecard.pr_url = pr_url

    return FixWriterResult(
        scorecard=scorecard,
        patches=patches,
        pr_body_markdown=pr_body,
        pr_url=pr_url,
    )


def _pr_title(scorecard: ReadinessScorecard) -> str:
    c = scorecard.counts
    return (
        f"LaunchGuard: {scorecard.verdict} — "
        f"{c.will_fail} will-fail / {c.will_misbehave} will-misbehave / "
        f"{c.cost_risk} cost-risk / {c.needs_review} needs-review"
    )


# ---------------------------------------------------------------------------
# PR body renderer (LLM-04 wraps this; deterministic baseline here)
# ---------------------------------------------------------------------------

def render_pr_body(
    scorecard: ReadinessScorecard,
    patches: list[Any],
    *,
    context: dict[str, Any] | None = None,
) -> str:
    """
    Render an evidence-grounded, redacted PR body (Markdown).

    LLM-04 SEAM (deferred): a Gemini 2.5 Pro pass may rewrite this into a more fluent
    narrative, but it MUST stay grounded in these deltas/evidence and pass redaction
    first (AI Operating Principles §3). The deterministic body below is the fallback and
    the ground truth the model is not allowed to contradict (no hallucinated commands).

    Patches map 1:1 to scorecard.deltas by index.
    """
    ctx = context or {}
    project = ctx.get("project_id", "(project)")
    service = ctx.get("service_name", "(service)")

    lines: list[str] = [
        f"## LaunchGuard readiness: **{scorecard.verdict}**",
        "",
        f"> {_VERDICT_HEADLINE.get(scorecard.verdict, scorecard.verdict)}",
        "",
        f"Target: `{service}` in project `{project}` — "
        "three-source contract reconciliation (code ⟷ deploy declaration ⟷ live GCP).",
        "",
        "| Class | Count |",
        "|---|---|",
        f"| will-fail | {scorecard.counts.will_fail} |",
        f"| will-misbehave | {scorecard.counts.will_misbehave} |",
        f"| cost-risk | {scorecard.counts.cost_risk} |",
        f"| needs-review | {scorecard.counts.needs_review} |",
        "",
        "### Findings",
        "",
    ]

    if not scorecard.deltas:
        lines.append("_No deltas detected across the three sources._")
    else:
        for i, delta in enumerate(scorecard.deltas):
            patch = patches[i] if i < len(patches) else None
            lines.append(f"#### `{delta.rule_id}` — {delta.delta_class} (confidence {delta.confidence:.2f})")
            lines.append("")
            lines.append(delta.summary)
            lines.append("")
            if delta.evidence:
                lines.append("**Evidence:**")
                for ev in delta.evidence:
                    lines.append(f"- `{ev.source}` @ `{ev.locator}` — {ev.snippet}")
                lines.append("")
            if patch is not None:
                lines.append(f"**Proposed fix** ({patch.kind}, never executed by LaunchGuard):")
                lines.append("")
                lines.append("```")
                lines.append(patch.content)
                lines.append("```")
                lines.append("")

    lines.append("---")
    lines.append(
        "_Every fix above is a proposal. LaunchGuard is read-only on cloud and opens this "
        "PR for human review — it never applies IAM/secret changes or merges (AI Operating "
        "Principles §1, §2). Secret values are redacted; only names/existence are shown (§3)._"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scorecard renderers (JSON + Markdown) — the headline artifact (BE-06)
# ---------------------------------------------------------------------------

def render_scorecard_json(scorecard: ReadinessScorecard) -> dict[str, Any]:
    """Return the scorecard as a contract-shaped dict (ReadinessScorecard)."""
    return scorecard.to_dict()


def render_scorecard_markdown(scorecard: ReadinessScorecard) -> str:
    """Render the readiness scorecard as standalone Markdown."""
    c = scorecard.counts
    lines = [
        "# LaunchGuard Readiness Scorecard",
        "",
        f"## Verdict: **{scorecard.verdict}**",
        "",
        f"> {_VERDICT_HEADLINE.get(scorecard.verdict, scorecard.verdict)}",
        "",
        "| Class | Count |",
        "|---|---|",
        f"| will-fail | {c.will_fail} |",
        f"| will-misbehave | {c.will_misbehave} |",
        f"| cost-risk | {c.cost_risk} |",
        f"| needs-review | {c.needs_review} |",
        "",
    ]
    if scorecard.pr_url:
        lines.append(f"**Fix PR:** {scorecard.pr_url}")
        lines.append("")
    lines.append("## Deltas")
    lines.append("")
    if not scorecard.deltas:
        lines.append("_None._")
    else:
        for d in scorecard.deltas:
            lines.append(
                f"- **{d.rule_id}** ({d.delta_class}, conf {d.confidence:.2f}): {d.summary}"
            )
    return "\n".join(lines)


def write_scorecard(
    scorecard: ReadinessScorecard,
    *,
    out_dir: Path | None = None,
    stem: str = "readiness",
) -> tuple[Path, Path]:
    """
    Write scorecard.json + scorecard.md to disk (default eval/scorecard/).

    Returns (json_path, md_path).
    """
    target = out_dir or (_repo_root() / "eval" / "scorecard")
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / f"{stem}.json"
    md_path = target / f"{stem}.md"
    json_path.write_text(
        json.dumps(render_scorecard_json(scorecard), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    md_path.write_text(render_scorecard_markdown(scorecard), encoding="utf-8")
    return json_path, md_path


__all__ = [
    "FixWriterResult",
    "render_pr_body",
    "render_scorecard_json",
    "render_scorecard_markdown",
    "run_fix_writer",
    "write_scorecard",
]
