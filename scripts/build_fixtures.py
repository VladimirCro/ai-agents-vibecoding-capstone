#!/usr/bin/env python3
"""
scripts/build_fixtures.py — BE-08 remainder: generate the eval fixture matrix.

Writes 8 additional fixtures (+ the existing hero) under fixtures/repos/<name>/ and
fixtures/gcp/<name>.json, each with a labels.json ground-truth file. Idempotent:
re-running overwrites the generated fixtures with identical bytes.

Each fixture is a minimal but realistic misconfigured Cloud Run repo paired with a
golden-JSON live snapshot whose state makes exactly the intended detector(s) fire.
One fixture (clean-control) is a true-negative: nothing should fire.

This is a BUILD-TIME generator, not part of the runtime package. It exists so the
fixture matrix is reviewable as data and reproducible. Run:  python scripts/build_fixtures.py

NEVER writes real secret values — only synthetic redacted names/existence.
"""

from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_REPOS = _ROOT / "fixtures" / "repos"
_GCP = _ROOT / "fixtures" / "gcp"

# A standard, healthy SA email reused across fixtures (synthetic).
_SA = "serviceAccount:svc@demo-project.iam.gserviceaccount.com"

_HEALTHY_DOCKERFILE = """\
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN adduser --system --group app
USER app
ENV PORT=8080
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
"""

_HEALTHY_APP = '''\
"""Minimal FastAPI-shaped fixture app. NO real secret values — names only."""
import os

DATABASE_URL = os.environ.get("DATABASE_URL")


def create_app():
    return {"/health": lambda: {"status": "ok"}, "/ready": lambda: {"status": "ready"}}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:create_app", host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
'''

_REQS = "uvicorn\nfastapi\n"


def _service_yaml(
    *,
    container_port: int = 8080,
    secrets: list[str] | None = None,
    liveness: bool = True,
    startup: bool = True,
    min_scale: int = 0,
    max_scale: int = 3,
    concurrency: int = 40,  # maxScale(3) x concurrency(40) = 120 < 200 threshold (healthy default)
    cpu_throttling: str = "true",
) -> str:
    secrets = secrets or []
    env_block = '            - name: PORT\n              value: "8080"\n'
    for s in secrets:
        env_block += (
            f"            - name: {s}\n"
            f"              valueFrom:\n"
            f"                secretKeyRef:\n"
            f"                  name: {s}\n"
            f"                  key: latest\n"
        )
    probes = ""
    if liveness:
        probes += (
            "          livenessProbe:\n"
            "            httpGet:\n"
            "              path: /health\n"
            "              port: 8080\n"
        )
    if startup:
        probes += (
            "          startupProbe:\n"
            "            httpGet:\n"
            "              path: /ready\n"
            "              port: 8080\n"
            "            failureThreshold: 12\n"
        )
    return f"""\
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: demo-service
  annotations:
    run.googleapis.com/minScale: "{min_scale}"
    run.googleapis.com/maxScale: "{max_scale}"
    run.googleapis.com/cpu-throttling: "{cpu_throttling}"
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/minScale: "{min_scale}"
        autoscaling.knative.dev/maxScale: "{max_scale}"
        run.googleapis.com/cpu-throttling: "{cpu_throttling}"
    spec:
      containerConcurrency: {concurrency}
      serviceAccountName: "svc@demo-project.iam.gserviceaccount.com"
      containers:
        - image: "gcr.io/demo-project/demo-service:latest"
          ports:
            - containerPort: {container_port}
          env:
{env_block}{probes}          resources:
            limits:
              memory: 512Mi
              cpu: "1"
"""


def _gcp_snapshot(
    *,
    secrets: list[dict] | None = None,
    sa_roles: list[str] | None = None,
    enabled_apis: list[str] | None = None,
) -> dict:
    return {
        "project_id": "demo-project",
        "runtime_sa": _SA,
        "sa_iam_roles": sa_roles
        or ["roles/run.invoker", "roles/secretmanager.secretAccessor",
            "roles/logging.logWriter"],
        "enabled_apis": enabled_apis
        or ["run.googleapis.com", "secretmanager.googleapis.com", "iam.googleapis.com"],
        "secrets": secrets or [],
        "mode": "fixture",
        "run_config": None,
    }


def _grant(name: str) -> dict:
    return {"name": name, "accessor_members": [_SA]}


def _no_grant(name: str) -> dict:
    return {"name": name, "accessor_members": []}


# ---------------------------------------------------------------------------
# Fixture definitions
# ---------------------------------------------------------------------------

def _write(name: str, files: dict[str, str], gcp: dict, labels: dict) -> None:
    repo = _REPOS / name
    repo.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    (repo / "labels.json").write_text(
        json.dumps(labels, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (_GCP / f"{name}.json").write_text(
        json.dumps(gcp, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def build_all() -> list[str]:
    _REPOS.mkdir(parents=True, exist_ok=True)
    _GCP.mkdir(parents=True, exist_ok=True)
    built: list[str] = []

    # 1. secret-declared-not-created (will-fail) ----------------------------- #
    name = "secret-not-created"
    app = _HEALTHY_APP.replace(
        'DATABASE_URL = os.environ.get("DATABASE_URL")',
        'STRIPE_API_KEY = os.environ.get("STRIPE_API_KEY")',
    )
    _write(
        name,
        {
            "Dockerfile": _HEALTHY_DOCKERFILE,
            "app.py": app,
            "requirements.txt": _REQS,
            "infra/cloud-run/service.yaml": _service_yaml(secrets=["STRIPE_API_KEY"]),
        },
        # STRIPE_API_KEY declared but NOT present in Secret Manager
        _gcp_snapshot(secrets=[]),
        {
            "fixture_name": name,
            "description": "Secret declared in service.yaml but never created in Secret Manager.",
            "expected_verdict": "BLOCKED",
            "expected_blockers": [
                {"rule_id": "secret-declared-not-created", "delta_class": "will-fail",
                 "note": "STRIPE_API_KEY referenced but absent from Live secrets."}
            ],
            "expected_warnings": [],
        },
    )
    built.append(name)

    # 2. port-mismatch (will-misbehave) ------------------------------------- #
    name = "port-mismatch"
    df = _HEALTHY_DOCKERFILE.replace("ENV PORT=8080", "ENV PORT=3000").replace(
        "EXPOSE 8080", "EXPOSE 3000"
    )
    _write(
        name,
        {
            "Dockerfile": df,
            "app.py": _HEALTHY_APP,
            "requirements.txt": _REQS,
            # declared containerPort 8080 but Dockerfile says 3000
            "infra/cloud-run/service.yaml": _service_yaml(container_port=8080),
        },
        _gcp_snapshot(),
        {
            "fixture_name": name,
            "description": "Dockerfile PORT=3000 but service.yaml containerPort=8080.",
            "expected_verdict": "WARN",
            "expected_blockers": [],
            "expected_warnings": [
                {"rule_id": "port-mismatch", "delta_class": "will-misbehave"}
            ],
        },
    )
    built.append(name)

    # 3. missing-health-probe (will-misbehave) ------------------------------ #
    name = "missing-health-probe"
    _write(
        name,
        {
            "Dockerfile": _HEALTHY_DOCKERFILE,
            "app.py": _HEALTHY_APP,  # exposes /health and /ready
            "requirements.txt": _REQS,
            # no liveness/startup probe declared
            "infra/cloud-run/service.yaml": _service_yaml(liveness=False, startup=False),
        },
        _gcp_snapshot(),
        {
            "fixture_name": name,
            "description": "App exposes /health and /ready but service.yaml has no probes.",
            "expected_verdict": "WARN",
            "expected_blockers": [],
            "expected_warnings": [
                {"rule_id": "missing-health-probe", "delta_class": "will-misbehave"},
                {"rule_id": "missing-startup-probe", "delta_class": "will-misbehave"},
            ],
        },
    )
    built.append(name)

    # 4. over-broad-sa-role (will-misbehave/security) ----------------------- #
    name = "over-broad-sa"
    _write(
        name,
        {
            "Dockerfile": _HEALTHY_DOCKERFILE,
            "app.py": _HEALTHY_APP,
            "requirements.txt": _REQS,
            "infra/cloud-run/service.yaml": _service_yaml(),
        },
        _gcp_snapshot(sa_roles=["roles/owner", "roles/run.invoker"]),
        {
            "fixture_name": name,
            "description": "Runtime SA holds roles/owner — over-broad (least-privilege violation).",
            "expected_verdict": "WARN",
            "expected_blockers": [],
            "expected_warnings": [
                {"rule_id": "over-broad-sa-role", "delta_class": "will-misbehave"}
            ],
        },
    )
    built.append(name)

    # 5. scaling-cost-flag (cost-risk) -------------------------------------- #
    name = "scaling-cost"
    _write(
        name,
        {
            "Dockerfile": _HEALTHY_DOCKERFILE,
            "app.py": _HEALTHY_APP,
            "requirements.txt": _REQS,
            "infra/cloud-run/service.yaml": _service_yaml(
                min_scale=20, max_scale=1000, concurrency=80, cpu_throttling="false"
            ),
        },
        _gcp_snapshot(),
        {
            "fixture_name": name,
            "description": "minScale=20, maxScale=1000, cpu-throttling off — cost risk.",
            "expected_verdict": "WARN",
            "expected_blockers": [],
            "expected_warnings": [
                {"rule_id": "scaling-cost-flag", "delta_class": "cost-risk"}
            ],
        },
    )
    built.append(name)

    # 6. api-not-enabled + missing-required-role (will-fail) ---------------- #
    name = "api-not-enabled"
    app = _HEALTHY_APP.replace(
        'DATABASE_URL = os.environ.get("DATABASE_URL")',
        "# Uses Vertex AI\nVERTEX_REGION = os.environ.get('VERTEX_REGION')",
    )
    snap = _gcp_snapshot(
        # aiplatform NOT enabled; SA lacks aiplatform.user
        enabled_apis=["run.googleapis.com", "secretmanager.googleapis.com"],
        sa_roles=["roles/run.invoker"],
    )
    _write(
        name,
        {
            "Dockerfile": _HEALTHY_DOCKERFILE,
            "app.py": app,
            "requirements.txt": _REQS + "google-cloud-aiplatform\n",
            # required_apis is populated via labels-driven contract injection below;
            # but the reconciler reads intended.required_apis. We mark it in a sidecar.
            "infra/cloud-run/service.yaml": _service_yaml(),
            "launchguard_required_apis.json": json.dumps(
                ["aiplatform.googleapis.com"]
            ),
        },
        snap,
        {
            "fixture_name": name,
            "description": "Code needs Vertex AI (aiplatform) but API not enabled + SA lacks role.",
            "expected_verdict": "BLOCKED",
            "required_apis": ["aiplatform.googleapis.com"],
            "expected_blockers": [
                {"rule_id": "api-not-enabled", "delta_class": "will-fail"},
                {"rule_id": "missing-required-role", "delta_class": "will-fail"},
            ],
            "expected_warnings": [],
        },
    )
    built.append(name)

    # 7. host-not-0.0.0.0 + pid1-signal-unsafe (will-misbehave) ------------- #
    name = "host-and-pid1"
    df = _HEALTHY_DOCKERFILE.replace(
        'CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]',
        "CMD uvicorn app:app --host 127.0.0.1 --port 8080",  # shell-form + loopback
    )
    app = _HEALTHY_APP.replace(
        'uvicorn.run("app:create_app", host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))',
        'uvicorn.run("app:create_app", host="127.0.0.1", port=int(os.environ.get("PORT", "8080")))',
    )
    _write(
        name,
        {
            "Dockerfile": df,
            "app.py": app,
            "requirements.txt": _REQS,
            "infra/cloud-run/service.yaml": _service_yaml(),
        },
        _gcp_snapshot(),
        {
            "fixture_name": name,
            "description": "App binds 127.0.0.1 (loopback) AND uses shell-form CMD (PID1 unsafe).",
            "expected_verdict": "WARN",
            "expected_blockers": [],
            "expected_warnings": [
                {"rule_id": "host-not-0.0.0.0", "delta_class": "will-misbehave"},
                {"rule_id": "pid1-signal-unsafe", "delta_class": "will-misbehave"},
            ],
        },
    )
    built.append(name)

    # 8. clean-control (true-negative) -------------------------------------- #
    name = "clean-control"
    _write(
        name,
        {
            "Dockerfile": _HEALTHY_DOCKERFILE,
            "app.py": _HEALTHY_APP,
            "requirements.txt": _REQS,
            "infra/cloud-run/service.yaml": _service_yaml(
                secrets=["DATABASE_URL"], min_scale=0, max_scale=3
            ),
        },
        # DATABASE_URL exists WITH accessor; SA least-privilege; nothing wrong
        _gcp_snapshot(secrets=[_grant("DATABASE_URL")]),
        {
            "fixture_name": name,
            "description": "Fully healthy repo — true-negative control. Nothing should fire.",
            "expected_verdict": "READY",
            "expected_blockers": [],
            "expected_warnings": [],
        },
    )
    built.append(name)

    return built


if __name__ == "__main__":
    names = build_all()
    print(f"Built {len(names)} fixtures:")
    for n in names:
        print(f"  - {n}")
