# LaunchGuard: BLOCKED — 1 will-fail / 0 will-misbehave / 1 cost-risk / 0 needs-review

- **Repo:** `example/worknote-ai`
- **Branch:** `launchguard/fix-readiness` (non-default — human merge gate)
- **Mode:** DRY-RUN (mock) — no network, no live mutation, never merged

## PR Body

## LaunchGuard readiness: **BLOCKED**

> BLOCKED — do not deploy until the will-fail findings are resolved.

Target: `(service)` in project `worknote-ai-like-project` — three-source contract reconciliation (code ⟷ deploy declaration ⟷ live GCP).

| Class | Count |
|---|---|
| will-fail | 1 |
| will-misbehave | 0 |
| cost-risk | 1 |
| needs-review | 0 |

### Findings

#### `secret-ref-without-secretAccessor` — will-fail (confidence 0.98)

[REDACTED] is referenced in both code (Intended) and service.yaml (Declared) but the runtime service account does not have roles/secretmanager.secretAccessor on it. The service will fail to start when Cloud Run attempts to inject the secret.

**Evidence:**
- `live` @ `secretmanager/JWT_SECRET_KEY/iam-policy` — accessor_members=[]
- `declared` @ `service.yaml/spec.template.spec.containers.env` — secretKeyRef.name=JWT_SECRET_KEY

**Proposed fix** (gcloud-command, never executed by LaunchGuard):

```
# Grant the runtime service account secretAccessor on 'JWT_SECRET_KEY'.
# Run this yourself after review — LaunchGuard never executes it.
gcloud secrets add-iam-policy-binding JWT_SECRET_KEY \
  --project=worknote-ai-like-project \
  --member='serviceAccount:worknote-ai-sa@worknote-ai-like-project.iam.gserviceaccount.com' \
  --role='roles/secretmanager.secretAccessor'
```

#### `scaling-cost-flag` — cost-risk (confidence 0.85)

Scaling configuration may result in unexpectedly high costs: maxScale=3 x concurrency=80 = 240 total concurrent requests (threshold: 200)

**Evidence:**
- `declared` @ `service.yaml/metadata.annotations` — minScale=0, maxScale=3, concurrency=80, cpu_throttling=True

**Proposed fix** (service-yaml-diff, never executed by LaunchGuard):

```
--- a/infra/cloud-run/service.yaml
+++ b/infra/cloud-run/service.yaml
@@ spec.template.metadata.annotations @@
-        autoscaling.knative.dev/maxScale: "1000"
+        autoscaling.knative.dev/maxScale: "10"
-        run.googleapis.com/cpu-throttling: "false"
+        run.googleapis.com/cpu-throttling: "true"
# Cap maxScale to a realistic ceiling and throttle idle CPU to control cost.
# (Advisory — review against your real traffic before applying.)
```

---
_Every fix above is a proposal. LaunchGuard is read-only on cloud and opens this PR for human review — it never applies IAM/secret changes or merges (AI Operating Principles §1, §2). Secret values are redacted; only names/existence are shown (§3)._

## Proposed Patches

> Every patch is a *proposal*. LaunchGuard never executes any of these (AI Operating Principles §1, §2). Apply them yourself after review.

### 1. `secret-ref-without-secretAccessor` (gcloud-command)

```
# Grant the runtime service account secretAccessor on 'JWT_SECRET_KEY'.
# Run this yourself after review — LaunchGuard never executes it.
gcloud secrets add-iam-policy-binding JWT_SECRET_KEY \
  --project=worknote-ai-like-project \
  --member='serviceAccount:worknote-ai-sa@worknote-ai-like-project.iam.gserviceaccount.com' \
  --role='roles/secretmanager.secretAccessor'
```
_applied=False (always false — never executed)_

### 2. `scaling-cost-flag` (service-yaml-diff)

```
--- a/infra/cloud-run/service.yaml
+++ b/infra/cloud-run/service.yaml
@@ spec.template.metadata.annotations @@
-        autoscaling.knative.dev/maxScale: "1000"
+        autoscaling.knative.dev/maxScale: "10"
-        run.googleapis.com/cpu-throttling: "false"
+        run.googleapis.com/cpu-throttling: "true"
# Cap maxScale to a realistic ceiling and throttle idle CPU to control cost.
# (Advisory — review against your real traffic before applying.)
```
_applied=False (always false — never executed)_
