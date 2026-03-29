---
work_package_id: WP05
title: Aggregate Model Training
lane: planned
dependencies: [WP02]
subtasks:
- T023
- T024
- T025
- T026
- T027
- T028
phase: Phase 3 - Advanced Features
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
- FR-003
- FR-010
---

# Work Package Prompt: WP05 -- Aggregate Model Training

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
spec-kitty implement WP05 --base WP02
```

Depends on WP02 (per-tenant training logic -- reuses the same model training functions and feature extraction). Does NOT depend on WP03 or WP04.

---

## Objectives & Success Criteria

- `CloudTrainer.train_aggregate()` pools events from all opted-in tenants and trains shared aggregate models
- Only data from explicitly opted-in tenants is included (FR-010)
- A sampling/weighting strategy prevents large tenants from dominating the model
- Aggregate weights are saved to a shared storage prefix (e.g., `__aggregate__/`)
- A minimum opt-in threshold logs a warning if too few tenants are available
- CLI `--mode cloud --aggregate` invokes aggregate training and prints a summary

## Context & Constraints

- **Spec**: `kitty-specs/004-cloud-training-pipeline/spec.md` -- User Story 3 (Aggregate Model Training), FR-003, FR-010
- **WP02 artifacts**: Feature extraction functions, model training flow, ModelStore saving
- **Privacy requirement**: The opt-in flag MUST be checked at query time, not cached. Only tenants who have explicitly opted in to data pooling contribute to the aggregate model.
- **Data volume**: Pooled data could be very large (thousands of tasks across many tenants). Sampling is essential.
- **Model reuse**: The same 5 model types are trained -- only the data source differs (pooled vs single-tenant).

### Key Architecture Decision

Aggregate models are stored at a "shared" or "aggregate" prefix in the ModelStore, separate from per-tenant models. The ModelStore protocol already supports `tenant_id=None` or a special ID. For aggregate models, use `tenant_id="__aggregate__"` as the namespace.

When the prediction API serves a Team-tier request, it loads both the per-user model AND the aggregate model and blends their predictions. The blending logic is NOT part of this WP -- it belongs to the prediction serving layer. This WP only produces the aggregate model weights.

---

## Subtasks & Detailed Guidance

### Subtask T023 -- Opt-in tenant discovery

- **Purpose**: Query the DataStore to find all tenants who have explicitly opted in to data pooling.
- **Steps**:
  1. Ensure the DataStore Protocol stub includes:
     ```python
     def list_opted_in_tenants(self) -> list[str]:
         """Return tenant IDs that have opted in to aggregate data pooling."""
         ...
     ```
  2. In `CloudTrainer`:
     ```python
     def _discover_opted_in_tenants(self) -> list[str]:
         """Get tenant IDs that have opted in to data pooling."""
         tenants = self.data_store.list_opted_in_tenants()
         logger.info("Found %d opted-in tenants for aggregate training", len(tenants))
         return tenants
     ```
  3. The opt-in status comes from the tenant's configuration in the database. The Go side (sigild) manages the opt-in flag. Python only reads it.
  4. The query MUST be fresh (not cached) to respect opt-out changes between training runs.
- **Files**: `src/sigil_ml/training/cloud_trainer.py`
- **Parallel?**: No -- feeds into T024.
- **Validation**:
  - [ ] Only opted-in tenants are returned
  - [ ] Tenants who opt out between runs are excluded
  - [ ] Empty list handled gracefully

### Subtask T024 -- Data pooling across tenants

- **Purpose**: Fetch training data from all opted-in tenants and combine into a single dataset.
- **Steps**:
  1. For each opted-in tenant, query completed tasks and events:
     ```python
     def _pool_training_data(self, tenant_ids: list[str]) -> dict:
         """Pool training data from multiple tenants.

         Returns:
             {
                 "tasks": list[dict],  # all tasks with tenant_id added
                 "task_events": dict[str, list[dict]],  # task_id -> events
                 "tenant_counts": dict[str, int],  # tenant_id -> task count
             }
         """
         all_tasks = []
         task_events = {}
         tenant_counts = {}

         for tenant_id in tenant_ids:
             tasks = self.data_store.query_completed_tasks(tenant_id)
             tenant_counts[tenant_id] = len(tasks)

             for task in tasks:
                 task["_tenant_id"] = tenant_id  # tag with source tenant
                 events = self.data_store.query_events_for_task(tenant_id, task["id"])
                 all_tasks.append(task)
                 task_events[task["id"]] = events

         return {
             "tasks": all_tasks,
             "task_events": task_events,
             "tenant_counts": tenant_counts,
         }
     ```
  2. Annotate each task with its source `_tenant_id` for sampling (T025) and debugging
  3. Memory concern: If pooled data is very large, consider streaming/batching. For the initial implementation, loading all into memory is acceptable if capped by sampling.
- **Files**: `src/sigil_ml/training/cloud_trainer.py`
- **Parallel?**: No -- sequential data loading per tenant.
- **Validation**:
  - [ ] Data from all opted-in tenants is included
  - [ ] Each task is tagged with its source tenant
  - [ ] tenant_counts accurately reflects per-tenant contribution

### Subtask T025 -- Sampling/weighting strategy

- **Purpose**: Prevent large tenants from dominating the aggregate model by implementing a sampling strategy that balances contributions.
- **Steps**:
  1. Implement a proportional sampling function:
     ```python
     MAX_TASKS_PER_TENANT = 1000  # Cap per tenant

     def _sample_pooled_data(
         self, pooled: dict, max_per_tenant: int = MAX_TASKS_PER_TENANT
     ) -> list[dict]:
         """Sample tasks from pooled data with per-tenant caps.

         Strategy: Each tenant contributes at most max_per_tenant tasks.
         If a tenant has fewer, all their tasks are included.
         Tasks are randomly sampled (seeded for reproducibility).
         """
         import random
         rng = random.Random(42)  # deterministic for reproducibility

         sampled_tasks = []
         for tenant_id, count in pooled["tenant_counts"].items():
             tenant_tasks = [t for t in pooled["tasks"] if t["_tenant_id"] == tenant_id]

             if len(tenant_tasks) > max_per_tenant:
                 tenant_tasks = rng.sample(tenant_tasks, max_per_tenant)
                 logger.info(
                     "Sampled %d/%d tasks from tenant %s",
                     max_per_tenant, count, tenant_id,
                 )

             sampled_tasks.extend(tenant_tasks)

         logger.info(
             "Aggregate dataset: %d tasks from %d tenants",
             len(sampled_tasks), len(pooled["tenant_counts"]),
         )
         return sampled_tasks
     ```
  2. The per-tenant cap ensures no single tenant contributes more than ~N% of the total (where N depends on tenant count).
  3. `MAX_TASKS_PER_TENANT` is configurable via environment variable:
     ```python
     MAX_TASKS_PER_TENANT = int(os.environ.get("SIGIL_ML_AGGREGATE_MAX_PER_TENANT", "1000"))
     ```
  4. Random seed (42) ensures reproducible sampling for debugging.
- **Files**: `src/sigil_ml/training/cloud_trainer.py`
- **Parallel?**: Independent design decision, can be developed alongside T023.
- **Edge Cases**:
  - One tenant has 10,000 tasks, another has 50: after sampling, first contributes 1000, second contributes 50
  - All tenants have fewer than the cap: no sampling needed, all data used
- **Validation**:
  - [ ] Tenant with 5000 tasks is capped at 1000
  - [ ] Tenant with 50 tasks keeps all 50
  - [ ] Sampling is deterministic (same results on re-run)
  - [ ] Total sample size is logged

### Subtask T026 -- Implement train_aggregate()

- **Purpose**: The main aggregate training method that orchestrates discovery, pooling, sampling, training, and weight saving.
- **Steps**:
  1. Implement in `CloudTrainer`:
     ```python
     AGGREGATE_TENANT_ID = "__aggregate__"

     def train_aggregate(self) -> TrainingRun:
         """Train aggregate models from pooled opted-in tenant data.

         Returns a TrainingRun with tenant_id="__aggregate__".
         """
         start = time.time()

         # 1. Discover opted-in tenants
         tenant_ids = self._discover_opted_in_tenants()
         if len(tenant_ids) < MIN_AGGREGATE_TENANTS:
             logger.warning(
                 "Only %d opted-in tenants (minimum recommended: %d). "
                 "Aggregate model may be unreliable.",
                 len(tenant_ids), MIN_AGGREGATE_TENANTS,
             )

         if not tenant_ids:
             return TrainingRun(
                 tenant_id=AGGREGATE_TENANT_ID,
                 status="skipped_threshold",
                 error_message="No opted-in tenants found",
             )

         # 2. Pool data from all opted-in tenants
         pooled = self._pool_training_data(tenant_ids)

         # 3. Apply sampling strategy
         sampled_tasks = self._sample_pooled_data(pooled)
         total_samples = len(sampled_tasks)

         # 4. Extract features and train all models
         models_trained = self._train_models_from_tasks(
             sampled_tasks,
             pooled["task_events"],
             tenant_id=AGGREGATE_TENANT_ID,
         )

         # 5. Record audit event
         elapsed = time.time() - start
         run = TrainingRun(
             tenant_id=AGGREGATE_TENANT_ID,
             status="trained",
             sample_count=total_samples,
             models_trained=models_trained,
             duration_sec=round(elapsed, 2),
         )

         self.data_store.record_training_event(AGGREGATE_TENANT_ID, {
             "kind": "aggregate_training",
             "tenants_pooled": len(tenant_ids),
             "sample_count": total_samples,
             "models_trained": models_trained,
             "duration_ms": int(elapsed * 1000),
             "ts": int(time.time() * 1000),
         })

         return run
     ```
  2. Factor out the model training logic from `train_tenant()` into a reusable `_train_models_from_tasks()` method that both per-tenant and aggregate paths use:
     ```python
     def _train_models_from_tasks(
         self, tasks: list[dict], task_events: dict, tenant_id: str,
     ) -> list[str]:
         """Train all model types from provided tasks and events.

         Returns list of model names that were successfully trained.
         """
         # Feature extraction -> model training -> save via ModelStore
         # This is the shared logic extracted from train_tenant()
     ```
  3. Save aggregate weights with `tenant_id=AGGREGATE_TENANT_ID` (`"__aggregate__"`)
- **Files**: `src/sigil_ml/training/cloud_trainer.py`
- **Parallel?**: No -- core implementation.
- **Validation**:
  - [ ] Aggregate training uses data from ALL opted-in tenants
  - [ ] Weights are saved with tenant_id="__aggregate__"
  - [ ] TrainingRun includes correct sample_count and models_trained
  - [ ] Audit event records tenants_pooled count
  - [ ] Zero opted-in tenants returns "skipped_threshold"

### Subtask T027 -- Wire CLI --aggregate to aggregate training

- **Purpose**: Connect the `--mode cloud --aggregate` CLI path to `CloudTrainer.train_aggregate()`.
- **Steps**:
  1. In `cli.py`, replace the placeholder from WP01:
     ```python
     elif args.aggregate:
         result = trainer.train_aggregate()
         # Wrap in summary for consistent output
         summary = TrainingSummary(
             mode="aggregate",
             total_tenants=1,
             trained=1 if result.status == "trained" else 0,
             skipped=1 if result.status.startswith("skipped") else 0,
             failed=1 if result.status == "failed" else 0,
             total_duration_sec=result.duration_sec,
             runs=[result],
         )
         print(summary.to_json())
     ```
  2. Exit code: 0 on success, 1 on failure
- **Files**: `src/sigil_ml/cli.py`
- **Parallel?**: No -- depends on T026.
- **Validation**:
  - [ ] `sigil-ml train --mode cloud --aggregate` prints JSON summary
  - [ ] Exit code 0 on successful aggregate training
  - [ ] Exit code 1 on failure

### Subtask T028 -- Minimum opt-in threshold check

- **Purpose**: Warn operators when too few tenants have opted in for meaningful aggregate models.
- **Steps**:
  1. Define `MIN_AGGREGATE_TENANTS = 3` constant (configurable via env var)
  2. Already integrated into T026's flow:
     ```python
     if len(tenant_ids) < MIN_AGGREGATE_TENANTS:
         logger.warning(
             "Only %d opted-in tenants (minimum recommended: %d).",
             len(tenant_ids), MIN_AGGREGATE_TENANTS,
         )
     ```
  3. The training still proceeds (it does not fail) -- the warning is informational
  4. Spec says "Given only 2 tenants have opted in, the job completes but logs a warning"
  5. The threshold is configurable:
     ```python
     MIN_AGGREGATE_TENANTS = int(os.environ.get("SIGIL_ML_MIN_AGGREGATE_TENANTS", "3"))
     ```
  6. Include the warning in the TrainingRun's metadata (or error_message field):
     ```python
     if len(tenant_ids) < MIN_AGGREGATE_TENANTS:
         run.error_message = (
             f"Warning: only {len(tenant_ids)} opted-in tenants "
             f"(recommended minimum: {MIN_AGGREGATE_TENANTS})"
         )
     ```
- **Files**: `src/sigil_ml/training/cloud_trainer.py`
- **Parallel?**: Yes -- can be developed alongside T026.
- **Validation**:
  - [ ] 2 opted-in tenants: warning logged, training proceeds, warning in output
  - [ ] 5 opted-in tenants: no warning
  - [ ] Custom threshold via env var is respected

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Privacy violation (non-opted-in data included) | Opt-in check at query time, not cached |
| Large tenant dominates aggregate model | Per-tenant sampling cap (T025) |
| Memory pressure from pooled data | Sampling reduces total data size; cap per tenant |
| Aggregate model quality with few tenants | Warning at threshold (T028); training still proceeds |
| Data leakage across tenants in model | Aggregate models are inherently shared -- document this in Team-tier agreement |

---

## Review Guidance

- Key acceptance checkpoints:
  1. ONLY opted-in tenant data is used (verify at query level)
  2. Sampling prevents any single tenant from contributing >N tasks
  3. Aggregate weights are saved to `__aggregate__` prefix, not a per-tenant prefix
  4. `_train_models_from_tasks()` is shared between per-tenant and aggregate paths (code reuse)
  5. Zero opted-in tenants is handled gracefully (not an error)
  6. Warning logged when below minimum threshold but training still completes
- Reviewers should trace the data flow: which DataStore methods are called, with what parameters, and verify opt-in is checked.

---

## Activity Log

- 2026-03-29T16:29:51Z -- system -- lane=planned -- Prompt created.
