#!/usr/bin/env python3
"""
Assemble the LaunchGuard submission video (<= 5 min, YouTube-ready) — no recording, no voice.

Renders caption cards (Pillow) covering the rubric's video points
(Problem -> Why agents -> Architecture -> Demo -> Build -> Results), splices in the recorded
terminal demo (assets/demo.mp4), and concatenates everything into assets/launchguard-video.mp4.

Usage:
    FFMPEG=/path/to/ffmpeg python scripts/build_video.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
BUILD = ASSETS / "_videobuild"
BUILD.mkdir(parents=True, exist_ok=True)

W, H = 1280, 720
BG = (13, 17, 23)          # GitHub dark
FG = (230, 237, 243)       # near-white
ACCENT = (46, 160, 160)    # teal
MUTED = (139, 148, 158)    # grey
DEMO = ASSETS / "demo.mp4"
OUT = ASSETS / "launchguard-video.mp4"

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")

_FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/ubuntu"]


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for d in _FONT_DIRS:
        p = Path(d) / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


F_TITLE = _font("DejaVuSans-Bold.ttf", 64)
F_H = _font("DejaVuSans-Bold.ttf", 46)
F_BODY = _font("DejaVuSans-Bold.ttf", 32)
F_MONO = _font("DejaVuSansMono.ttf", 28)
F_SMALL = _font("DejaVuSans-Bold.ttf", 24)


def card(path: Path, *, kicker: str, title: str, lines: list[tuple[str, tuple[int, int, int], object]]) -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # accent bar
    d.rectangle([0, 0, 12, H], fill=ACCENT)
    y = 90
    if kicker:
        d.text((90, y), kicker.upper(), font=F_SMALL, fill=ACCENT)
        y += 46
    d.text((90, y), title, font=F_H, fill=FG)
    y += 86
    for text, color, font in lines:
        d.text((90, y), text, font=font, fill=color)
        y += int(font.size * 1.55)
    d.text((90, H - 70), "github.com/VladimirCro/ai-agents-vibecoding-capstone   ·   CC-BY 4.0",
           font=F_SMALL, fill=MUTED)
    img.save(path)


def title_card(path: Path) -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 12, H], fill=ACCENT)
    d.text((90, 250), "LaunchGuard", font=F_TITLE, fill=FG)
    d.text((92, 340), "Cloud Run deploy-readiness agent", font=F_H, fill=ACCENT)
    d.text((92, 430), "Three-source contract reconciliation", font=F_BODY, fill=FG)
    d.text((92, 480), "Google ADK + Gemini  ·  track: Agents for Business", font=F_BODY, fill=MUTED)
    img.save(path)


# (seconds, builder) — keep total well under 5 minutes
CARDS: list[tuple[float, str]] = []


def build_cards() -> list[tuple[Path, float]]:
    seq: list[tuple[Path, float]] = []

    p = BUILD / "00_title.png"; title_card(p); seq.append((p, 5))

    p = BUILD / "01_problem.png"
    card(p, kicker="The problem", title="Deploy goes green. First request 500s.",
         lines=[
             ("A linter sees only the repo.", FG, F_BODY),
             ("GCP Security Review sees only the live cloud.", FG, F_BODY),
             ("'gcloud deploy' sees only the declared config.", FG, F_BODY),
             ("", FG, F_BODY),
             ("No tool sees all three at once — so silent", MUTED, F_BODY),
             ("misconfigurations ship to production.", MUTED, F_BODY),
         ]); seq.append((p, 8))

    p = BUILD / "02_idea.png"
    card(p, kicker="Why an agent", title="Reconcile three sources of truth",
         lines=[
             ("INTENDED  — what the code requires", ACCENT, F_MONO),
             ("DECLARED  — what the deploy declares", ACCENT, F_MONO),
             ("LIVE      — what GCP actually grants  (read-only)", ACCENT, F_MONO),
             ("", FG, F_BODY),
             ("The value is the DELTA between the three —", FG, F_BODY),
             ("classified will-fail / will-misbehave / cost-risk.", FG, F_BODY),
         ]); seq.append((p, 9))

    p = BUILD / "03_arch.png"
    card(p, kicker="Architecture (multi-agent, ADK)", title="Orchestrator + 4 sub-agents",
         lines=[
             ("RepoAuditor        -> IntendedContract", FG, F_MONO),
             ("GcpStateInspector  -> LiveState (read-only gcloud)", FG, F_MONO),
             ("Declared parser    -> DeclaredState", FG, F_MONO),
             ("Reconciler  (core) -> ReconciliationDelta[]  (11 detectors)", ACCENT, F_MONO),
             ("FixWriter          -> scorecard + fix PR (human-in-the-loop)", FG, F_MONO),
             ("", FG, F_BODY),
             ("Handoff via ADK session state.", MUTED, F_BODY),
         ]); seq.append((p, 10))

    p = BUILD / "04_demo.png"
    card(p, kicker="Demo", title="LaunchGuard on a real Cloud Run service",
         lines=[
             ("Real worknote-ai-staging  ->  WARN (no false positives)", FG, F_BODY),
             ("One IAM grant dropped     ->  BLOCKED (the killer)", FG, F_BODY),
             ("", FG, F_BODY),
             ("Deterministic — no API key, reproducible.", MUTED, F_BODY),
         ]); seq.append((p, 5))

    # ---- demo.mp4 spliced here by the assembler ----

    p = BUILD / "05_build.png"
    card(p, kicker="The build", title="Security & deployability are the spine",
         lines=[
             ("Read-only on cloud  (mutating verb rejected pre-exec)", FG, F_BODY),
             ("Changes only as a PR  ·  secret redaction before the model", FG, F_BODY),
             ("Golden-JSON fixture replay  ·  pgvector incident memory", FG, F_BODY),
             ("Docker + Cloud Run target  ·  gcloud / GitHub tools", FG, F_BODY),
         ]); seq.append((p, 8))

    p = BUILD / "06_results.png"
    card(p, kicker="Results", title="Eval scorecard",
         lines=[
             ("9 fixtures  ·  precision / recall / F1 = 1.00", ACCENT, F_BODY),
             ("0 false positives on the clean control", FG, F_BODY),
             ("285 tests green  (ruff + mypy + pytest)", FG, F_BODY),
             ("Course concepts: Multi-agent (ADK) · Security · Deployability", MUTED, F_BODY),
         ]); seq.append((p, 8))

    p = BUILD / "07_close.png"
    card(p, kicker="LaunchGuard", title="Catch silent-misbehave before deploy",
         lines=[
             ("Three-source contract reconciliation for Cloud Run.", FG, F_BODY),
             ("Read-only. PR-gated. Reproducible.", ACCENT, F_BODY),
         ]); seq.append((p, 5))

    return seq


def png_to_clip(png: Path, seconds: float, out: Path) -> None:
    subprocess.run([
        FFMPEG, "-y", "-loop", "1", "-t", str(seconds), "-i", str(png),
        "-r", "30", "-vf", f"scale={W}:{H},format=yuv420p",
        "-c:v", "mpeg4", "-q:v", "3", "-pix_fmt", "yuv420p", str(out),
    ], check=True, capture_output=True)


def demo_to_clip(out: Path) -> None:
    subprocess.run([
        FFMPEG, "-y", "-i", str(DEMO),
        "-vf", (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0d1117,format=yuv420p"),
        "-r", "30", "-c:v", "mpeg4", "-q:v", "3", "-pix_fmt", "yuv420p",
        "-an", str(out),
    ], check=True, capture_output=True)


def main() -> int:
    cards = build_cards()
    clips: list[Path] = []
    for i, (png, sec) in enumerate(cards):
        clip = BUILD / f"clip_{i:02d}.mp4"
        png_to_clip(png, sec, clip)
        clips.append(clip)
        # splice the demo right after the "Demo" card (index 4 -> file 04_demo)
        if png.name == "04_demo.png":
            demo_clip = BUILD / "clip_demo.mp4"
            demo_to_clip(demo_clip)
            clips.append(demo_clip)

    concat = BUILD / "concat.txt"
    concat.write_text("".join(f"file '{c}'\n" for c in clips), encoding="utf-8")
    subprocess.run([
        FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
        "-c", "copy", str(OUT),
    ], check=True, capture_output=True)

    print(f"OK -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
