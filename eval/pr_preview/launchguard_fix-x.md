# t

- **Repo:** `example/repo`
- **Branch:** `launchguard/fix-x` (non-default — human merge gate)
- **Mode:** DRY-RUN (mock) — no network, no live mutation, never merged

## PR Body

b

## Proposed Patches

> Every patch is a *proposal*. LaunchGuard never executes any of these (AI Operating Principles §1, §2). Apply them yourself after review.

### 1. `secret-ref-without-secretAccessor` (gcloud-command)

```
# Grant the runtime service account secretAccessor on 'JWT_SECRET_KEY'.
# Run this yourself after review — LaunchGuard never executes it.
gcloud secrets add-iam-policy-binding JWT_SECRET_KEY \
  --project=<PROJECT_ID> \
  --member='serviceAccount:<RUNTIME_SA_EMAIL>' \
  --role='roles/secretmanager.secretAccessor'
```
_applied=False (always false — never executed)_
