---
work_package_id: WP02
title: Per-Tenant Training Logic
lane: planned
dependencies: [WP01]
subtasks:
- T007
- T008
- T009
- T010
- T011
- T012
phase: Phase 1 - Foundation
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
- FR-004
- FR-005
- FR-006
- FR-009
- FR-012
---

# Work Package Prompt: WP02 -- Per-Tenant Training Logic

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
spec-kitty implement WP02 --base WP01
```

Depends on WP01 (CloudTrainer skeleton and TrainingRun dataclass).

---

## Objectives & Success Criteria

- `CloudTrainer.train_tenant(tenant_id)` fully implements per-tenant training:
  - Checks data threshold (minimum 10 completed tasks) -- uses synthetic fallback below threshold
  - Enforces minimum interval (default 1 hour) between retraining runs
  - Extracts features from DataStore queries
  - Trains all 5 model types (stuck, duration, activity, workflow, quality)
  - Saves weights via ModelStore with tenant-scoped prefix
  - Records training audit events via DataStore
  - Returns a complete `TrainingRun` result
- Existing model `.train()` methods are reused without modification
- Local training path remains unchanged

## Context & Constraints

- **Spec**: `kitty-specs/004-cloud-training-pipeline/spec.md` -- User Stories 1 and 4, FR-004, FR-005, FR-006, FR-009, FR-012
- **WP01 artifacts**: `src/sigil_ml/training/cloud_trainer.py` (CloudTrainer skeleton), `src/sigil_ml/training/models.py` (TrainingRun/TrainingSummary)
- **Current trainer**: `src/sigil_ml/training/trainer.py` -- reference implementation for SQLite-based training
- **Feature extraction**: `src/sigil_ml/features.py` -- `extract_stuck_features()` and `extract_duration_features()` currently take a `db_path` and open SQLite directly. These need DataStore-compatible equivalents.
- **Synthetic generators**: `src/sigil_ml/training/synthetic.py` -- `generate_stuck_data()` and `generate_duration_data()` exist. Activity, workflow, and quality models need cold-start strategies.
- **Model classes**: `src/sigil_ml/models/stuck.py`, `duration.py`, `activity.py`, `workflow.py`, `quality.py` -- each has a `.train()` method that accepts numpy arrays or structured data.

### Key Design Decisions

1. **Feature extraction refactor**: Create new functions in `features.py` (or a new `cloud_features.py`) that accept DataStore query results (list of dicts) instead of a db_path. The existing functions remain untouched for local mode.

2. **Model training reuse**: The model classes' `.train(X, y)` methods are the shared training logic. CloudTrainer prepares X and y from DataStore data and calls the same methods. It does NOT call `Trainer._train_stuck()` etc. directly because those contain SQLite queries.

3. **Model saving via ModelStore**: After training, serialize the model (via `joblib.dump` to bytes) and call `model_store.save(model_name, data, tenant_id=tenant_id)`. The current models call `config.weights_path()` and `joblib.dump()` directly -- CloudTrainer must bypass that and use ModelStore.

---

## Subtasks & Detailed Guidance

### Subtask T007 -- Data threshold check

- **Purpose**: Implement the minimum data threshold: at least 10 completed tasks are required for ML training. Below this threshold, the system falls back to synthetic data (FR-005).
- **Steps**:
  1. In `CloudTrainer.train_tenant()`, query completed tasks:
     ```python
     tasks = self.data_store.query_completed_tasks(tenant_id)
     has_sufficient_data = len(tasks) >= MIN_TASKS_THRESHOLD
     ```
  2. Define `MIN_TASKS_THRESHOLD = 10` as a module-level constant in `cloud_trainer.py`
  3. If below threshold, set a flag `use_synthetic = True` that T008 will handle
  4. If above threshold, proceed with real data extraction
  5. The threshold check should return the task list regardless (needed for other decisions)
- **Files**: `src/sigil_ml/training/cloud_trainer.py`
- **Parallel?**: No -- part of the main `train_tenant()` flow.
- **Validation**:
  - [ ] Tenant with 15 tasks proceeds to real training
  - [ ] Tenant with 5 tasks triggers synthetic fallback
  - [ ] Tenant with exactly 10 tasks proceeds to real training (boundary case)

### Subtask T008 -- Cold-start synthetic data fallback

- **Purpose**: When a tenant has insufficient real data, train models using synthetic data generators so the tenant still gets valid model weights (matching local cold-start behavior).
- **Steps**:
  1. Reuse existing generators: `generate_stuck_data(500)` and `generate_duration_data(500)` from `training/synthetic.py`
  2. Create new generators for activity and workflow models (or document why they're not needed):
     - **Activity**: Rule-based classifier (no ML training needed for cold-start). Skip training, the model defaults to rules.
     - **Workflow**: Same as activity -- rule-based by default. Skip training.
     - **Quality**: Weight-based, no sklearn model. Skip training (uses default weights).
  3. In `train_tenant()`, when `use_synthetic = True`:
     ```python
     if use_synthetic:
         # Train stuck model with synthetic data
         X_stuck, y_stuck = generate_stuck_data(500)
         stuck_model = StuckPredictor()
         stuck_model.train(X_stuck, y_stuck)
         # Save via ModelStore
         self._save_model(stuck_model, "stuck", tenant_id)

         # Train duration model with synthetic data
         X_dur, y_dur = generate_duration_data(500)
         dur_model = DurationEstimator()
         dur_model.train(X_dur, y_dur)
         self._save_model(dur_model, "duration", tenant_id)

         # Activity, workflow, quality: skip -- they work rule-based
         models_trained = ["stuck", "duration"]
     ```
  4. Ensure the `TrainingRun.status` reflects synthetic usage (e.g., `"trained_synthetic"` or `"trained"` with a note)
- **Files**: `src/sigil_ml/training/cloud_trainer.py`, `src/sigil_ml/training/synthetic.py` (read only)
- **Parallel?**: Independent from T009/T010 but part of the same method.
- **Notes**: The spec says "models are trained using synthetic data (matching local cold-start behavior)" -- the local `Trainer._train_stuck()` already does this when `len(rows) < 10`. Mirror that behavior.

### Subtask T009 -- Minimum interval enforcement

- **Purpose**: Prevent excessive retraining by skipping tenants that were trained within the last configurable interval (default 1 hour, FR-006).
- **Steps**:
  1. Add `MIN_RETRAIN_INTERVAL_SEC = 3600` constant to `cloud_trainer.py`
  2. In `train_tenant()`, check the last training timestamp:
     ```python
     last_ts = self.data_store.get_last_training_ts(tenant_id)
     if last_ts is not None:
         elapsed = time.time() - (last_ts / 1000.0)  # assuming ms timestamps
         if elapsed < MIN_RETRAIN_INTERVAL_SEC:
             logger.info("Skipping tenant %s: trained %d sec ago", tenant_id, int(elapsed))
             return TrainingRun(
                 tenant_id=tenant_id,
                 status="skipped_interval",
                 duration_sec=0.0,
             )
     ```
  3. Allow the interval to be configurable via environment variable `SIGIL_ML_RETRAIN_INTERVAL_SEC`
  4. The check must happen before expensive data queries
- **Files**: `src/sigil_ml/training/cloud_trainer.py`
- **Parallel?**: Independent check, but part of the `train_tenant()` flow.
- **Validation**:
  - [ ] Tenant trained 30 minutes ago is skipped with `status="skipped_interval"`
  - [ ] Tenant trained 2 hours ago proceeds normally
  - [ ] Tenant never trained before proceeds normally (`last_ts is None`)
  - [ ] Custom interval via env var is respected

### Subtask T010 -- Feature extraction via DataStore

- **Purpose**: Create DataStore-compatible feature extraction functions that produce the same feature dictionaries as the existing SQLite-based extractors.
- **Steps**:
  1. Create new functions (in `features.py` or a new `cloud_features.py`) that accept pre-queried data:
     ```python
     def extract_stuck_features_from_data(
         task: dict, events: list[dict]
     ) -> dict[str, float]:
         """Extract stuck features from pre-queried task and events data.

         Same output as extract_stuck_features() but without SQLite dependency.
         """
         now_ms = int(time.time() * 1000)
         started_at = task.get("started_at", now_ms)
         last_active = task.get("last_active", now_ms)
         session_length_sec = max((last_active - started_at) / 1000.0, 1.0)
         test_failure_count = float(task.get("test_fails", 0) or 0)

         # ... (mirror logic from extract_stuck_features)
         # The logic is identical, but operates on passed-in data
         # instead of querying SQLite
     ```
  2. Similarly create `extract_duration_features_from_data(task, events)`:
     ```python
     def extract_duration_features_from_data(
         task: dict, events: list[dict]
     ) -> dict[str, float]:
         """Extract duration features from pre-queried data."""
         # Mirror logic from extract_duration_features()
     ```
  3. In `CloudTrainer.train_tenant()`, use these new functions:
     ```python
     for task in tasks:
         events = self.data_store.query_events_for_task(tenant_id, task["id"])
         stuck_feats = extract_stuck_features_from_data(task, events)
         dur_feats = extract_duration_features_from_data(task, events)
         # Collect into X matrices
     ```
  4. The original `extract_stuck_features(db_path, task_id)` and `extract_duration_features(db_path, task_id)` remain unchanged for local mode
- **Files**: `src/sigil_ml/features.py` (add new functions) or `src/sigil_ml/cloud_features.py` (new file)
- **Parallel?**: Can proceed alongside T007/T009 since it adds new functions without modifying existing ones.
- **Notes**:
  - The existing extractors contain ~60 lines of feature logic each. The new versions should be nearly identical, just operating on dict inputs instead of querying SQLite.
  - Consider factoring out the shared logic into internal helpers to reduce duplication. For example, `_compute_stuck_features(task_dict, events_list)` called by both the SQLite-based and DataStore-based versions.
- **Validation**:
  - [ ] Given the same task and events data, both extractors produce identical feature dictionaries
  - [ ] New extractors handle missing fields gracefully (return defaults)
  - [ ] New extractors handle empty event lists

### Subtask T011 -- Train all 5 model types per tenant

- **Purpose**: Complete the training pipeline to train all 5 model types (stuck, duration, activity, workflow, quality) using real tenant data and save weights via ModelStore.
- **Steps**:
  1. In `CloudTrainer.train_tenant()`, after feature extraction:
     ```python
     models_trained = []

     # 1. Stuck predictor
     if len(X_stuck) >= MIN_TASKS_THRESHOLD:
         stuck = StuckPredictor()
         stuck.train(X_stuck, y_stuck)
         self._save_model_weights("stuck", stuck, tenant_id)
         models_trained.append("stuck")

     # 2. Duration estimator
     if len(X_duration) >= MIN_TASKS_THRESHOLD:
         duration = DurationEstimator()
         duration.train(X_duration, y_duration)
         self._save_model_weights("duration", duration, tenant_id)
         models_trained.append("duration")

     # 3. Activity classifier
     # Needs classified events with labels -- use activity features
     if len(events_with_labels) >= 200:
         activity = ActivityClassifier()
         activity.train(X_activity, y_activity)
         self._save_model_weights("activity", activity, tenant_id)
         models_trained.append("activity")

     # 4. Workflow state predictor
     if len(workflow_samples) >= 50:
         workflow = WorkflowStatePredictor()
         workflow.train(X_workflow, y_workflow)
         self._save_model_weights("workflow", workflow, tenant_id)
         models_trained.append("workflow")

     # 5. Quality estimator
     if len(quality_outcomes) >= 5:
         quality = QualityEstimator()
         quality.train(quality_outcomes)
         self._save_model_weights("quality", quality, tenant_id)
         models_trained.append("quality")
     ```
  2. Implement `_save_model_weights()` helper:
     ```python
     def _save_model_weights(self, model_name: str, model: Any, tenant_id: str) -> None:
         """Serialize model and save via ModelStore."""
         import io
         import joblib
         buf = io.BytesIO()
         joblib.dump(model, buf)
         self.model_store.save(model_name, buf.getvalue(), tenant_id=tenant_id)
     ```
  3. Note: The model classes currently call `config.weights_path()` and `joblib.dump()` internally in their `.train()` methods. For cloud training, we must either:
     - (a) Call `.train()` and then separately serialize and save via ModelStore (the internal save is a no-op or goes to a temp location)
     - (b) Modify model classes to accept an optional ModelStore parameter
     - Approach (a) is simpler for this WP. The model's internal save goes to the local temp dir (or default XDG dir), and CloudTrainer additionally saves via ModelStore. The local file can be ignored.
  4. Label generation for activity and workflow models requires heuristics from the existing data -- document the labeling strategy:
     - **Activity labels**: Use rule-based classifier to label events, then train ML model on those labels (bootstrapping)
     - **Workflow labels**: Derive from task outcomes (completed quickly = "deep_work" session, many test failures = "blocked" session)
     - **Quality labels**: Use task speed scores as training targets
- **Files**: `src/sigil_ml/training/cloud_trainer.py`
- **Parallel?**: No -- depends on T010 (feature extraction) and T008 (fallback path).
- **Validation**:
  - [ ] All 5 models attempted when sufficient data exists
  - [ ] Models with insufficient data are skipped (not failed)
  - [ ] `TrainingRun.models_trained` accurately reflects which models were trained
  - [ ] Weights are saved via ModelStore.save() with correct tenant_id

### Subtask T012 -- Record training audit events

- **Purpose**: Record audit trail of training runs via DataStore, matching the pattern from `TrainingScheduler._log_retrain()` (FR-009).
- **Steps**:
  1. At the end of `train_tenant()`, after training completes:
     ```python
     self.data_store.record_training_event(tenant_id, {
         "kind": "training",
         "status": run.status,
         "sample_count": run.sample_count,
         "models_trained": run.models_trained,
         "duration_ms": int(run.duration_sec * 1000),
         "ts": int(time.time() * 1000),
     })
     ```
  2. Also record on failure (status="failed") so failures are auditable
  3. The event structure should be compatible with the existing `ml_events` table schema:
     - `kind`: "training"
     - `endpoint`: "cloud_trainer"
     - `routing`: tenant_id
     - `latency_ms`: training duration
     - `ts`: timestamp
- **Files**: `src/sigil_ml/training/cloud_trainer.py`
- **Parallel?**: No -- final step in `train_tenant()`.
- **Validation**:
  - [ ] After training, `record_training_event()` is called on the DataStore
  - [ ] Event includes tenant_id, status, sample_count, duration
  - [ ] Failed training runs also produce audit events

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Feature extraction refactor introduces bugs | Keep original functions untouched; new functions mirror the logic exactly |
| Activity/workflow labeling is imprecise | Use bootstrapping (rule-based labels for initial training); labels improve as model quality improves |
| Model internal save conflicts with ModelStore save | Let internal save go to temp/default dir; ModelStore save is the authoritative cloud path |
| DataStore API doesn't match Protocol stubs | Protocol stubs document expectations; update when real interface lands |

---

## Review Guidance

- Key acceptance checkpoints:
  1. `train_tenant()` handles all three paths: real data, synthetic fallback, interval skip
  2. Feature extraction functions produce identical outputs to existing SQLite-based versions
  3. All 5 model types are trained when data is sufficient
  4. Weights are saved via ModelStore (not direct filesystem writes)
  5. Audit events are recorded for both success and failure
  6. No modifications to existing `Trainer` class, `TrainingScheduler`, or local feature extractors
- Reviewers should check that the training flow order is: interval check -> threshold check -> feature extraction -> model training -> save weights -> audit event

---

## Activity Log

- 2026-03-29T16:29:51Z -- system -- lane=planned -- Prompt created.
