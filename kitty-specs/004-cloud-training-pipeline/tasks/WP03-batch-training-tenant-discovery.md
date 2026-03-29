---
work_package_id: WP03
title: Batch Training & Tenant Discovery
lane: planned
dependencies: [WP02]
subtasks:
- T013
- T014
- T015
- T016
- T017
phase: Phase 2 - Story Delivery
assignee: ''
agent: ''
shell_pid: ''
review_status: ''
reviewed_by: ''
history:
- timestamp: '2026-03-29T16:29:51Z'
  lane: planned
  agent: system
  shell_pid: ''
  action: Prompt generated via /spec-kitty.tasks
requirement_refs:
- FR-002
- FR-007
- FR-008
---

# Work Package Prompt: WP03 -- Batch Training & Tenant Discovery

## Important: Review Feedback Status

**Read this first if you are implementing this task!**

- **Has review feedback?**: Check the `review_status` field above. If it says `has_feedback`, scroll to the **Review Feedback** section immediately.
- **You must address all feedback** before your work is complete.
- **Mark as acknowledged**: When you understand the feedback and begin addressing it, update `review_status: acknowledged` in the frontmatter.

---

## Review Feedback

> **Populated by `/spec-kitty.review`** -- Reviewers add detailed feedback here when work needs changes.

*[This section is empty initially.]*

---

## Markdown Formatting
Wrap HTML/XML tags in backticks: `` `<div>` ``, `` `<script>` ``
Use language identifiers in code blocks: ````python`, ````bash`

---

## Implementation Command

```bash
spec-kitty implement WP03 --base WP02
```

Depends on WP02 (per-tenant training logic -- `train_tenant()` must be fully implemented).

---

## Objectives & Success Criteria

- `CloudTrainer.train_all_tenants()` discovers all eligible tenants and trains each sequentially
- Per-tenant failures are caught and logged without interrupting the batch
- A `TrainingSummary` with trained/skipped/failed counts and per-tenant details is returned
- The "nothing to train" case (all tenants skipped) completes successfully with a clean summary
- CLI `--mode cloud --all-tenants` invokes batch training and prints the summary

## Context & Constraints

- **Spec**: `kitty-specs/004-cloud-training-pipeline/spec.md` -- User Story 2 (All-Tenant Batch Training), FR-002, FR-007, FR-008
- **WP01/WP02 artifacts**: `CloudTrainer` with working `train_tenant()`, `TrainingRun`, `TrainingSummary` dataclasses
- **DataStore dependency**: Must provide a `list_tenants()` method (or equivalent). If the DataStore protocol from feature 002 doesn't include this, extend the Protocol stub.
- **Sequential execution**: Batch training processes tenants one at a time. No parallelism in this WP (future optimization).

---

## Subtasks & Detailed Guidance

### Subtask T013 -- Tenant discovery from DataStore

- **Purpose**: Discover all tenant IDs that have synced data and are candidates for training.
- **Steps**:
  1. Ensure the DataStore Protocol stub (from WP01) includes:
     ```python
     def list_tenants(self) -> list[str]:
         """Return all tenant IDs that have synced data."""
         ...
     ```
  2. If needed, add a more specific method that filters to tenants with enough data:
     ```python
     def list_eligible_tenants(self, min_tasks: int = 0) -> list[str]:
         """Return tenant IDs with at least min_tasks completed tasks."""
         ...
     ```
  3. However, the simpler approach is to list all tenants and let `train_tenant()` handle eligibility (threshold/interval checks already in WP02). This is more maintainable.
  4. In `CloudTrainer`:
     ```python
     def _discover_tenants(self) -> list[str]:
         """Get all tenant IDs from the DataStore."""
         tenants = self.data_store.list_tenants()
         logger.info("Discovered %d tenants", len(tenants))
         return tenants
     ```
- **Files**: `src/sigil_ml/training/cloud_trainer.py`
- **Parallel?**: No -- feeds into T014.
- **Validation**:
  - [ ] Returns a list of tenant ID strings
  - [ ] Empty list is handled (no tenants found)
  - [ ] Logs the count of discovered tenants

### Subtask T014 -- Implement train_all_tenants()

- **Purpose**: The core batch training method that iterates all tenants, calls `train_tenant()` for each, and collects results into a `TrainingSummary`.
- **Steps**:
  1. Implement in `CloudTrainer`:
     ```python
     def train_all_tenants(self) -> TrainingSummary:
         """Discover and train all eligible tenants in batch.

         Returns a TrainingSummary with per-tenant results.
         Failures for individual tenants do not interrupt the batch.
         """
         start = time.time()
         tenants = self._discover_tenants()

         summary = TrainingSummary(
             mode="batch",
             total_tenants=len(tenants),
         )

         for tenant_id in tenants:
             run = self._train_tenant_safe(tenant_id)
             summary.runs.append(run)

             if run.status == "trained" or run.status == "trained_synthetic":
                 summary.trained += 1
             elif run.status.startswith("skipped"):
                 summary.skipped += 1
             elif run.status == "failed":
                 summary.failed += 1

         summary.total_duration_sec = round(time.time() - start, 2)
         return summary
     ```
  2. The `_train_tenant_safe()` wrapper handles fault isolation (see T015)
  3. The method must work correctly for edge cases:
     - Zero tenants: Returns summary with all counts at 0
     - All tenants skipped: Returns summary with `trained=0, skipped=N`
     - Mix of outcomes: Accurate counts for each status
- **Files**: `src/sigil_ml/training/cloud_trainer.py`
- **Parallel?**: No -- core implementation.
- **Validation**:
  - [ ] 10 tenants, 8 eligible: summary shows `trained=8, skipped=2`
  - [ ] 0 tenants: summary shows all zeros, no crash
  - [ ] All skipped: summary shows `trained=0, skipped=N`, exit code 0
  - [ ] Mix of trained/skipped/failed: accurate counts

### Subtask T015 -- Fault isolation per tenant

- **Purpose**: Ensure that one tenant's training failure does not prevent training of remaining tenants (FR-007).
- **Steps**:
  1. Create `_train_tenant_safe()` wrapper:
     ```python
     def _train_tenant_safe(self, tenant_id: str) -> TrainingRun:
         """Train a tenant with full error isolation.

         Catches all exceptions, logs them, and returns a failed TrainingRun
         instead of propagating the error.
         """
         try:
             return self.train_tenant(tenant_id)
         except Exception as e:
             logger.error(
                 "Training failed for tenant %s: %s",
                 tenant_id, str(e),
                 exc_info=True,
             )
             return TrainingRun(
                 tenant_id=tenant_id,
                 status="failed",
                 error_message=str(e),
             )
     ```
  2. Key requirements:
     - Catches ALL exceptions (including KeyboardInterrupt is debatable -- for CronJobs, catching Exception is sufficient)
     - Logs the full traceback for debugging
     - Returns a `TrainingRun` with `status="failed"` and `error_message`
     - Never allows one tenant's failure to prevent processing of the next tenant
  3. The error message should be actionable: include the exception type and message, but truncate if very long (max 500 chars)
- **Files**: `src/sigil_ml/training/cloud_trainer.py`
- **Parallel?**: No -- wraps train_tenant().
- **Notes**: The spec explicitly requires this: "The failure is logged, that tenant is skipped, and remaining tenants continue training."
- **Validation**:
  - [ ] If train_tenant() raises ValueError for tenant A, tenant B still trains
  - [ ] If train_tenant() raises ConnectionError for tenant A, tenant B still trains
  - [ ] Failed tenant appears in summary with `status="failed"` and error message
  - [ ] The traceback appears in log output (stderr)

### Subtask T016 -- Wire CLI --all-tenants to batch training

- **Purpose**: Connect the `--mode cloud --all-tenants` CLI path to `CloudTrainer.train_all_tenants()` and display the summary.
- **Steps**:
  1. In `cli.py`, replace the placeholder from WP01:
     ```python
     elif args.all_tenants:
         summary = trainer.train_all_tenants()
         print(summary.to_json())
         # Exit code: 0 if no failures, 1 if any failures
         if summary.failed > 0:
             sys.exit(1)
     ```
  2. Consider the exit code strategy:
     - `0`: All tenants trained or skipped (no failures)
     - `1`: At least one tenant failed (some may have succeeded)
     - This allows CronJob monitoring to detect partial failures
  3. Add a `--max-tenants` flag for partial batches (useful for testing and gradual rollout):
     ```python
     train_parser.add_argument(
         "--max-tenants", type=int, default=None,
         help="Limit batch to N tenants (for testing/gradual rollout)"
     )
     ```
- **Files**: `src/sigil_ml/cli.py`
- **Parallel?**: No -- depends on T014.
- **Validation**:
  - [ ] `sigil-ml train --mode cloud --all-tenants` prints JSON summary to stdout
  - [ ] Exit code 0 when all tenants succeed or are skipped
  - [ ] Exit code 1 when any tenant fails
  - [ ] `--max-tenants 5` limits the batch to 5 tenants

### Subtask T017 -- Create TrainingBatch dataclass (if needed)

- **Purpose**: If WP01's `TrainingSummary` doesn't already cover all batch-specific needs, extend or create a dedicated `TrainingBatch` dataclass.
- **Steps**:
  1. Review the `TrainingSummary` dataclass from WP01
  2. If it already has `runs: list[TrainingRun]` with trained/skipped/failed counts, it is sufficient -- no new class needed
  3. If additional fields are needed for batch-specific info, add them:
     ```python
     @dataclass
     class TrainingSummary:
         # ... existing fields ...
         # Additional batch fields:
         tenants_discovered: int = 0  # total before filtering
         tenants_processed: int = 0   # total actually attempted
         max_tenants_limit: int | None = None  # --max-tenants value if set
     ```
  4. The spec mentions "TrainingBatch" as a separate entity. If `TrainingSummary` with `mode="batch"` covers the use case, document the mapping and avoid a separate class.
- **Files**: `src/sigil_ml/training/models.py`
- **Parallel?**: Yes -- only dataclass changes, can proceed alongside T013/T014.
- **Validation**:
  - [ ] Dataclass serializes to JSON with all required fields
  - [ ] Batch-specific fields (tenants_discovered, etc.) are present
  - [ ] Compatible with `train_all_tenants()` output

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| DataStore.list_tenants() not available | Add to Protocol stub; document expected behavior |
| Batch takes longer than CronJob timeout | Log per-tenant progress; add --max-tenants for partial batches |
| One tenant corrupts shared state | train_tenant() must be fully isolated -- no shared mutable state |
| Memory pressure from many tenants | Sequential processing prevents accumulation; model objects are GC'd between tenants |

---

## Review Guidance

- Key acceptance checkpoints:
  1. `train_all_tenants()` processes every tenant regardless of individual failures
  2. Summary accurately reflects trained/skipped/failed counts
  3. Error messages in failed runs are actionable
  4. Zero-tenant case doesn't crash
  5. CLI exit codes follow the documented strategy
  6. Per-tenant progress is logged for operational visibility
- Reviewers should verify the fault isolation by imagining a `DataStore.query_completed_tasks()` that raises for one specific tenant -- the batch must survive this.

---

## Activity Log

- 2026-03-29T16:29:51Z -- system -- lane=planned -- Prompt created.
