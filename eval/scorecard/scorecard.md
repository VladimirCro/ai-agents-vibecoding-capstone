# LaunchGuard Eval Scorecard

## Headline

> Caught 4/4 blockers across 9 fixture(s); precision=1.00 / recall=1.00 / F1=1.00; 0 false-positive blocker(s) on the control set; warning coverage 8/8

| Metric | Value |
|---|---|
| Fixtures | 9 |
| Verdict match | 9/9 |
| Blocker precision | 100.00% |
| Blocker recall | 100.00% |
| Blocker F1 | 100.00% |
| Blocker true positives | 4 |
| Blocker false positives | 0 |
| Blocker false negatives | 0 |
| Warning coverage | 8/8 (100%) |

## Per-Fixture Results

### `api-not-enabled` — PASS

- Verdict: `BLOCKED` (expected `BLOCKED`) — match
- Precision: 100.00% | Recall: 100.00%
- Expected blockers: ['api-not-enabled', 'missing-required-role']
- Detected blockers: ['api-not-enabled', 'missing-required-role']
- True positives: ['api-not-enabled', 'missing-required-role']

### `clean-control` — PASS

- Verdict: `READY` (expected `READY`) — match
- Precision: 100.00% | Recall: 100.00%
- Expected blockers: (none)
- Detected blockers: (none)

### `host-and-pid1` — PASS

- Verdict: `WARN` (expected `WARN`) — match
- Precision: 100.00% | Recall: 100.00%
- Expected blockers: (none)
- Detected blockers: (none)
- Expected warnings: ['host-not-0.0.0.0', 'pid1-signal-unsafe']
- Detected warnings: ['host-not-0.0.0.0', 'pid1-signal-unsafe']

### `missing-health-probe` — PASS

- Verdict: `WARN` (expected `WARN`) — match
- Precision: 100.00% | Recall: 100.00%
- Expected blockers: (none)
- Detected blockers: (none)
- Expected warnings: ['missing-health-probe', 'missing-startup-probe']
- Detected warnings: ['missing-health-probe', 'missing-startup-probe']

### `over-broad-sa` — PASS

- Verdict: `WARN` (expected `WARN`) — match
- Precision: 100.00% | Recall: 100.00%
- Expected blockers: (none)
- Detected blockers: (none)
- Expected warnings: ['over-broad-sa-role']
- Detected warnings: ['over-broad-sa-role']

### `port-mismatch` — PASS

- Verdict: `WARN` (expected `WARN`) — match
- Precision: 100.00% | Recall: 100.00%
- Expected blockers: (none)
- Detected blockers: (none)
- Expected warnings: ['port-mismatch']
- Detected warnings: ['port-mismatch']

### `scaling-cost` — PASS

- Verdict: `WARN` (expected `WARN`) — match
- Precision: 100.00% | Recall: 100.00%
- Expected blockers: (none)
- Detected blockers: (none)
- Expected warnings: ['scaling-cost-flag']
- Detected warnings: ['scaling-cost-flag']

### `secret-not-created` — PASS

- Verdict: `BLOCKED` (expected `BLOCKED`) — match
- Precision: 100.00% | Recall: 100.00%
- Expected blockers: ['secret-declared-not-created']
- Detected blockers: ['secret-declared-not-created']
- True positives: ['secret-declared-not-created']

### `worknote-ai-like` — PASS

- Verdict: `BLOCKED` (expected `BLOCKED`) — match
- Precision: 100.00% | Recall: 100.00%
- Expected blockers: ['secret-ref-without-secretAccessor']
- Detected blockers: ['secret-ref-without-secretAccessor']
- True positives: ['secret-ref-without-secretAccessor']
- Expected warnings: ['scaling-cost-flag']
- Detected warnings: ['scaling-cost-flag']
