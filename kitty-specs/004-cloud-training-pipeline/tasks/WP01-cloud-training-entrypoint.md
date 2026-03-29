---
work_package_id: WP01
title: Cloud Training Entrypoint & CLI
lane: planned
dependencies: []
subtasks:
- T001
- T002
- T003
- T004
- T005
- T006
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
- FR-001
- FR-004
- FR-011
- FR-012
---

# Work Package Prompt: WP01 -- Cloud Training Entrypoint & CLI

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
spec-kitty implement WP01
```

No dependencies -- this is the starting work package.

---

## Objectives & Success Criteria

- The `sigil-ml train` CLI gains `--mode`, `--tenant`, `--all-tenants`, and `--aggregate` flags
- A new `CloudTrainer` class exists that accepts `DataStore` and `ModelStore` as constructor dependencies
- `CloudTrainer.train_tenant(tenant_id)` returns a `TrainingRun` result
- Running `sigil-ml train` with no cloud flags produces identical behavior to current implementation (FR-011)
- Running `sigil-ml train --mode cloud --tenant <id>` invokes CloudTrainer and prints structured output

## Context & Constraints

- **Spec**: `kitty-specs/004-cloud-training-pipeline/spec.md`
- **Codebase entry point**: `src/sigil_ml/cli.py` -- current CLI with `serve`, `train`, and `health-check` subcommands
- **Current trainer**: `src/sigil_ml/training/trainer.py` -- `Trainer` class that reads SQLite directly
- **Dependencies**: Features 002 (DataStore protocol) and 003 (ModelStore protocol) provide the interfaces. If they are not yet implemented, use `typing.Protocol` stubs matching the expected API.
- **Constraint**: No heavyweight dependencies beyond what is already in `pyproject.toml`. DataStore and ModelStore are protocol-based, so no new runtime imports are needed for this WP.

### Key Architecture Decision

The `CloudTrainer` wraps the same model training algorithms as the local `Trainer` but sources data from `DataStore` and persists weights via `ModelStore`. It does NOT subclass `Trainer` -- it is a parallel implementation that reuses the model classes' `.train()` methods directly. This avoids entangling local and cloud code paths.

---

## Subtasks & Detailed Guidance

### Subtask T001 -- Extend CLI with cloud training flags

- **Purpose**: Add the `--mode`, `--tenant`, `--all-tenants`, and `--aggregate` flags to the existing `train` subcommand so that operators can invoke cloud training from the command line.
- **Steps**:
  1. Open `src/sigil_ml/cli.py`
  2. Add arguments to the existing `train_parser`:
     ```python
     train_parser.add_argument(
         "--mode", choices=["local", "cloud"], default="local",
         help="Training mode: local (SQLite) or cloud (Postgres/S3)"
     )
     train_parser.add_argument(
         "--tenant", type=str, default=None,
         help="Train models for a specific tenant ID (cloud mode only)"
     )
     train_parser.add_argument(
         "--all-tenants", action="store_true", default=False,
         help="Discover and train all eligible tenants (cloud mode only)"
     )
     train_parser.add_argument(
         "--aggregate", action="store_true", default=False,
         help="Train aggregate models from pooled opted-in data (cloud mode only)"
     )
     ```
  3. Add validation in the `train` command handler:
     - If `--mode cloud` is used, at least one of `--tenant`, `--all-tenants`, or `--aggregate` must be provided
     - `--tenant` and `--all-tenants` are mutually exclusive
     - If `--mode local`, the cloud flags must not be present (or are ignored with a warning)
  4. The local path (`--mode local` or no `--mode` flag) must continue to use the existing `Trainer` class exactly as it does today
- **Files**: `src/sigil_ml/cli.py`
- **Parallel?**: No -- this is the CLI skeleton other subtasks wire into.
- **Validation**:
  - [ ] `sigil-ml train --help` shows new flags
  - [ ] `sigil-ml train --mode cloud` without --tenant/--all-tenants/--aggregate prints an error
  - [ ] `sigil-ml train --mode cloud --tenant X --all-tenants` prints mutual exclusivity error
  - [ ] `sigil-ml train` (no flags) still works identically to current behavior

### Subtask T002 -- Create CloudTrainer class skeleton

- **Purpose**: Establish the `CloudTrainer` class that will orchestrate all cloud training operations. It accepts abstract `DataStore` and `ModelStore` interfaces, making it testable with mocks.
- **Steps**:
  1. Create `src/sigil_ml/training/cloud_trainer.py`
  2. Define the class:
     ```python
     """Cloud training orchestrator using DataStore and ModelStore abstractions."""

     import logging
     import time
     from typing import Protocol, runtime_checkable

     logger = logging.getLogger(__name__)


     @runtime_checkable
     class DataStore(Protocol):
         """Data access interface (from feature 002).

         Stub for development. Replace with import from sigil_ml.storage
         when feature 002 is implemented.
         """
         def query_completed_tasks(self, tenant_id: str, since_ts: int | None = None) -> list[dict]: ...
         def query_events_for_task(self, tenant_id: str, task_id: str) -> list[dict]: ...
         def get_last_training_ts(self, tenant_id: str) -> int | None: ...
         def record_training_event(self, tenant_id: str, event: dict) -> None: ...
         def list_tenants(self) -> list[str]: ...


     @runtime_checkable
     class ModelStore(Protocol):
         """Model weight storage interface (from feature 003).

         Stub for development. Replace with import from sigil_ml.model_storage
         when feature 003 is implemented.
         """
         def load(self, model_name: str, tenant_id: str | None = None) -> bytes | None: ...
         def save(self, model_name: str, data: bytes, tenant_id: str | None = None) -> None: ...


     class CloudTrainer:
         """Orchestrates model training for cloud deployments.

         Uses DataStore for reading training data and ModelStore for
         persisting trained model weights. Supports per-tenant and
         aggregate training modes.
         """

         def __init__(self, data_store: DataStore, model_store: ModelStore) -> None:
             self.data_store = data_store
             self.model_store = model_store

         def train_tenant(self, tenant_id: str) -> "TrainingRun":
             """Train all models for a single tenant."""
             raise NotImplementedError("Implemented in WP02")

         def train_all_tenants(self) -> "TrainingBatch":
             """Discover and train all eligible tenants."""
             raise NotImplementedError("Implemented in WP03")

         def train_aggregate(self) -> "TrainingRun":
             """Train aggregate models from pooled opted-in data."""
             raise NotImplementedError("Implemented in WP05")
     ```
  3. The Protocol stubs should closely match what features 002 and 003 will provide. When those features land, replace the local Protocol definitions with imports.
- **Files**: `src/sigil_ml/training/cloud_trainer.py` (new)
- **Parallel?**: No -- T003 and T005 depend on this.
- **Notes**: The Protocol stubs serve as a contract. If features 002/003 change their API, these stubs document what CloudTrainer expects and where to update.

### Subtask T003 -- Implement train_tenant() method (skeleton)

- **Purpose**: Provide the initial `train_tenant()` implementation that queries data, trains models, saves weights, and returns a `TrainingRun`. The full training logic (threshold checks, interval enforcement) comes in WP02 -- this WP provides the structural scaffolding.
- **Steps**:
  1. In `CloudTrainer.train_tenant()`:
     ```python
     def train_tenant(self, tenant_id: str) -> TrainingRun:
         start = time.time()
         logger.info("Starting training for tenant %s", tenant_id)

         # Query completed tasks
         tasks = self.data_store.query_completed_tasks(tenant_id)
         sample_count = len(tasks)

         # TODO (WP02): threshold check, interval check, feature extraction
         # TODO (WP02): train all 5 model types

         elapsed = time.time() - start
         run = TrainingRun(
             tenant_id=tenant_id,
             status="trained",
             sample_count=sample_count,
             models_trained=[],  # populated in WP02
             duration_sec=round(elapsed, 2),
         )

         logger.info("Training complete for tenant %s: %s", tenant_id, run)
         return run
     ```
  2. This skeleton validates the end-to-end wiring (CLI -> CloudTrainer -> DataStore -> result) without implementing the actual training logic.
- **Files**: `src/sigil_ml/training/cloud_trainer.py`
- **Parallel?**: No -- depends on T002.
- **Notes**: The skeleton returns a valid `TrainingRun` so that T005 (CLI wiring) can be tested end-to-end immediately.

### Subtask T004 -- Create TrainingRun and TrainingSummary dataclasses

- **Purpose**: Define the structured data types that represent training results. These are used throughout the training pipeline and for structured output.
- **Steps**:
  1. Create `src/sigil_ml/training/models.py`:
     ```python
     """Data models for training pipeline results."""

     from __future__ import annotations

     import json
     from dataclasses import dataclass, field
     from typing import Any


     @dataclass
     class TrainingRun:
         """Result of training models for a single tenant."""
         tenant_id: str
         status: str  # "trained", "skipped_threshold", "skipped_interval", "skipped_locked", "failed"
         sample_count: int = 0
         models_trained: list[str] = field(default_factory=list)
         duration_sec: float = 0.0
         error_message: str | None = None

         def to_dict(self) -> dict[str, Any]:
             return {
                 "tenant_id": self.tenant_id,
                 "status": self.status,
                 "sample_count": self.sample_count,
                 "models_trained": self.models_trained,
                 "duration_sec": self.duration_sec,
                 "error_message": self.error_message,
             }

         def to_json(self) -> str:
             return json.dumps(self.to_dict(), indent=2)


     @dataclass
     class TrainingSummary:
         """Structured summary of a batch or aggregate training run."""
         mode: str  # "batch", "aggregate", "single"
         total_tenants: int = 0
         trained: int = 0
         skipped: int = 0
         failed: int = 0
         total_duration_sec: float = 0.0
         runs: list[TrainingRun] = field(default_factory=list)

         def to_dict(self) -> dict[str, Any]:
             return {
                 "mode": self.mode,
                 "total_tenants": self.total_tenants,
                 "trained": self.trained,
                 "skipped": self.skipped,
                 "failed": self.failed,
                 "total_duration_sec": self.total_duration_sec,
                 "runs": [r.to_dict() for r in self.runs],
             }

         def to_json(self) -> str:
             return json.dumps(self.to_dict(), indent=2)
     ```
  2. Note: `TrainingBatch` mentioned in the spec is equivalent to `TrainingSummary` with `mode="batch"`. A single class covers all summary use cases.
- **Files**: `src/sigil_ml/training/models.py` (new)
- **Parallel?**: Yes -- this defines data structures only, no dependencies on other subtasks.
- **Validation**:
  - [ ] `TrainingRun` serializes to valid JSON
  - [ ] `TrainingSummary` includes a list of `TrainingRun` objects
  - [ ] All fields have sensible defaults

### Subtask T005 -- Wire CLI --tenant to CloudTrainer

- **Purpose**: Connect the CLI `--mode cloud --tenant <id>` path to the `CloudTrainer.train_tenant()` method and print the result.
- **Steps**:
  1. In `cli.py`, in the `train` command handler, add the cloud path:
     ```python
     elif args.command == "train":
         if args.mode == "cloud":
             # Validate cloud flags
             if not (args.tenant or args.all_tenants or args.aggregate):
                 parser.error("Cloud mode requires --tenant, --all-tenants, or --aggregate")
             if args.tenant and args.all_tenants:
                 parser.error("--tenant and --all-tenants are mutually exclusive")

             # Initialize DataStore and ModelStore from config/env
             # TODO: Replace stubs with real implementations when features 002/003 land
             from sigil_ml.training.cloud_trainer import CloudTrainer
             data_store = _create_data_store()  # factory function
             model_store = _create_model_store()  # factory function
             trainer = CloudTrainer(data_store, model_store)

             if args.tenant:
                 result = trainer.train_tenant(args.tenant)
                 print(result.to_json())
             elif args.all_tenants:
                 # Wired in WP03
                 print("Batch training not yet implemented")
                 sys.exit(1)
             elif args.aggregate:
                 # Wired in WP05
                 print("Aggregate training not yet implemented")
                 sys.exit(1)
         else:
             # Existing local training path -- UNCHANGED
             db = args.db or str(config.db_path())
             print(f"Training models from {db} ...")
             trainer = Trainer(db)
             result = trainer.train_all()
             print(f"Done: {result}")
     ```
  2. Create factory functions `_create_data_store()` and `_create_model_store()` in `cli.py` (or a separate `factory.py`). Initially these can return stub implementations that raise `NotImplementedError` or return minimal mock data for testing the wiring.
  3. The factory functions should read configuration from environment variables:
     - `SIGIL_ML_POSTGRES_URL` for DataStore
     - `SIGIL_ML_S3_BUCKET`, `SIGIL_ML_S3_REGION` for ModelStore
- **Files**: `src/sigil_ml/cli.py`
- **Parallel?**: No -- depends on T001 (CLI flags), T002 (CloudTrainer), T004 (TrainingRun).
- **Validation**:
  - [ ] `sigil-ml train --mode cloud --tenant test-1` prints JSON output
  - [ ] The JSON output contains `tenant_id`, `status`, `sample_count`, `models_trained`, `duration_sec`
  - [ ] Exit code is 0 on success

### Subtask T006 -- Ensure local training unchanged

- **Purpose**: Verify that the existing local training path is completely unaffected by the new cloud training code. This satisfies FR-011.
- **Steps**:
  1. Verify the existing `elif args.command == "train"` path in `cli.py` still executes when `--mode local` or no `--mode` flag is provided
  2. Verify `Trainer` class in `training/trainer.py` is NOT modified by this WP
  3. Verify `TrainingScheduler` in `training/scheduler.py` is NOT modified by this WP
  4. Run existing tests: `pytest tests/test_models.py` -- all must pass
  5. Run `sigil-ml train --db /tmp/test.db` manually -- should work as before (will fail if DB doesn't exist, but the code path is exercised)
- **Files**: `src/sigil_ml/cli.py` (verify only), `src/sigil_ml/training/trainer.py` (verify unchanged), `src/sigil_ml/training/scheduler.py` (verify unchanged)
- **Parallel?**: No -- validation step after T001-T005.
- **Validation**:
  - [ ] `pytest tests/` passes with no regressions
  - [ ] `sigil-ml train --help` shows new flags without breaking old behavior
  - [ ] `Trainer` class has no diff from before this WP
  - [ ] `TrainingScheduler` class has no diff from before this WP

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Features 002/003 not yet implemented | Use Protocol stubs; replace with imports when features land |
| CLI flag conflicts with future flags | Use argparse mutually exclusive groups |
| Breaking local training | T006 is an explicit validation gate |
| DataStore/ModelStore API mismatch | Protocol stubs serve as contract documentation; update when real interfaces are known |

---

## Review Guidance

- Key acceptance checkpoints:
  1. CLI flags parse correctly with validation
  2. `CloudTrainer` accepts abstract interfaces (not concrete implementations)
  3. `TrainingRun` and `TrainingSummary` serialize to valid JSON
  4. Local training path has zero changes
  5. All existing tests pass
- Reviewers should verify that no `import sqlite3` or `import boto3` appears in `cloud_trainer.py` -- it must be backend-agnostic.

---

## Activity Log

- 2026-03-29T16:29:51Z -- system -- lane=planned -- Prompt created.
