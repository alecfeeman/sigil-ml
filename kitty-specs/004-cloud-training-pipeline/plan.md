# Implementation Plan: Cloud Training Pipeline

**Branch**: `004-cloud-training-pipeline` | **Date**: 2026-03-30 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/kitty-specs/004-cloud-training-pipeline/spec.md`

## Summary

Add a cloud-oriented training entrypoint to sigil-ml that runs as a K8s CronJob. The pipeline reads synced event data from Postgres (via DataStore, feature 002), trains per-tenant models using the same algorithms as local mode, optionally trains aggregate models from pooled opted-in data, and saves weights to S3 (via ModelStore, feature 003). The existing local training path remains unchanged. The CLI is extended with `sigil-ml train --mode cloud` subcommands. Concurrent training prevention uses Postgres advisory locks.

## Technical Context

**Language/Version**: Python 3.10+
**Primary Dependencies**: FastAPI, scikit-learn, numpy, joblib, uvicorn (existing); psycopg2 (added by feature 002 for Postgres)
**Storage**: PostgreSQL (read events/tasks via DataStore), S3 (write model weights via ModelStore), SQLite (local mode, unchanged)
**Testing**: pytest with mock DataStore/ModelStore implementations
**Target Platform**: Linux containers (K8s CronJob) for cloud; macOS/Linux/Windows for local
**Project Type**: Single project (extending existing CLI + training modules)
**Performance Goals**: Batch training of 100 tenants completes within 30 minutes (SC-002 from spec)
**Constraints**: No new heavyweight dependencies beyond what features 002/003 introduce; structured JSON logging to stdout; no external logging/observability deps
**Scale/Scope**: Hundreds of tenants, each with 10-10,000 completed tasks

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| Minimal dependency set | PASS | No new deps beyond what 002/003 already add (psycopg2, boto3). Training uses existing scikit-learn/numpy/joblib. |
| pytest for all tests | PASS | All new code tested with pytest using mock DataStore/ModelStore. |
| Security-first, local-only for local mode | PASS | Local training unchanged. Cloud training only runs when explicitly invoked with `--mode cloud`. No data leaves the machine in local mode. |
| Simplicity over complexity | PASS | Reuses existing Trainer algorithms. Cloud orchestration is a thin layer on top. Postgres advisory locks for concurrency (no external deps). |
| Cross-platform | PASS | Cloud training is Linux-targeted (K8s), but the code itself is platform-agnostic Python. Local training unchanged on all platforms. |
| Docstrings on all classes and public functions | PASS | Will follow existing codebase conventions. |

## Project Structure

### Documentation (this feature)

```
kitty-specs/004-cloud-training-pipeline/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── contracts/           # Phase 1 output (CLI contract)
└── tasks.md             # Phase 2 output (via /spec-kitty.tasks)
```

### Source Code (repository root)

```
src/sigil_ml/
├── cli.py                          # MODIFY: add `train --mode cloud` subcommands
├── config.py                       # MODIFY: add cloud training config (env vars)
├── training/
│   ├── __init__.py
│   ├── trainer.py                  # MODIFY: accept DataStore + ModelStore instead of db_path
│   ├── scheduler.py                # MODIFY: accept DataStore instead of db_path
│   ├── synthetic.py                # UNCHANGED
│   ├── cloud_trainer.py            # NEW: CloudTrainer orchestrates per-tenant + aggregate
│   ├── tenant_discovery.py         # NEW: discover eligible tenants from DataStore
│   └── locking.py                  # NEW: Postgres advisory lock helpers
├── storage/                        # NEW (feature 002 creates this)
│   ├── __init__.py
│   ├── datastore.py                # DataStore protocol (feature 002)
│   ├── sqlite_store.py             # SqliteStore (feature 002)
│   └── postgres_store.py           # PostgresStore (feature 002)
├── model_storage/                  # NEW (feature 003 creates this)
│   ├── __init__.py
│   ├── model_store.py              # ModelStore protocol (feature 003)
│   ├── local_store.py              # LocalModelStore (feature 003)
│   └── s3_store.py                 # S3ModelStore (feature 003)
├── models/                         # MODIFY: accept ModelStore in train()/load()
│   ├── stuck.py
│   ├── duration.py
│   ├── activity.py
│   ├── quality.py
│   └── workflow.py
├── features.py                     # MODIFY: accept DataStore instead of db_path
├── app.py                          # UNCHANGED (cloud training is CLI-only, not server)
├── poller.py                       # UNCHANGED by this feature
├── routes.py                       # UNCHANGED by this feature
└── schema.py                       # UNCHANGED

tests/
├── test_cloud_trainer.py           # NEW: CloudTrainer unit tests
├── test_tenant_discovery.py        # NEW: tenant discovery tests
├── test_locking.py                 # NEW: advisory lock tests
├── test_trainer_refactor.py        # NEW: verify Trainer works with DataStore/ModelStore
├── test_features.py                # EXISTING (may need updates if features.py changes)
├── test_models.py                  # EXISTING (may need updates for ModelStore)
└── test_server.py                  # EXISTING (no changes expected)
```

**Structure Decision**: Single project, extending the existing `src/sigil_ml/` layout. New cloud training logic lives in `training/` alongside the existing `trainer.py` and `scheduler.py`. The `storage/` and `model_storage/` directories are created by features 002 and 003 respectively; this feature codes against their protocols.

## Complexity Tracking

No constitution violations. The design stays within the established patterns:
- Cloud training is additive (new files + modifications, no deletions)
- Reuses existing model algorithms via Trainer
- No new heavyweight dependencies
- Advisory locks are a Postgres-native feature, not an external system

## Design Decisions

### D1: Trainer Refactoring Strategy

The current `Trainer` class takes a `db_path: str | Path` and directly queries SQLite. Features 002/003 introduce `DataStore` and `ModelStore` protocols. This feature must refactor `Trainer` to accept these protocols.

**Approach**: Modify `Trainer.__init__` to accept a `DataStore` and `ModelStore`. The `Trainer` class becomes backend-agnostic. A factory function creates the appropriate stores based on mode. The existing `sigil-ml train` (local mode) constructs a `SqliteStore` + `LocalModelStore` and passes them to `Trainer` -- behavior is identical.

```python
class Trainer:
    def __init__(self, data_store: DataStore, model_store: ModelStore) -> None:
        self.data_store = data_store
        self.model_store = model_store
```

The `features.py` functions (`extract_stuck_features`, `extract_duration_features`) currently take `db_path` and open SQLite connections directly. These must also be refactored to work through `DataStore`. The DataStore protocol will need methods like `get_task(task_id)`, `get_events_for_task(task_id)`, and `get_completed_tasks()` that these functions can call.

### D2: CloudTrainer Orchestration

`CloudTrainer` is the top-level orchestrator for cloud training. It handles:

1. **Tenant discovery**: Query DataStore for distinct tenant IDs with sufficient data
2. **Eligibility check**: Skip tenants trained within the minimum interval
3. **Locking**: Acquire Postgres advisory lock per tenant before training
4. **Training**: Delegate to `Trainer` with tenant-scoped DataStore + ModelStore
5. **Fault isolation**: Catch per-tenant exceptions, log, continue
6. **Summary**: Produce structured JSON output

```python
class CloudTrainer:
    def __init__(self, data_store: DataStore, model_store: ModelStore, config: CloudTrainingConfig) -> None:
        ...

    def train_tenant(self, tenant_id: str) -> TrainingRun:
        """Train all models for a single tenant."""

    def train_all_tenants(self) -> TrainingBatch:
        """Discover and train all eligible tenants."""

    def train_aggregate(self) -> TrainingRun:
        """Train aggregate models from pooled opted-in tenant data."""
```

### D3: Tenant-Scoped DataStore and ModelStore

The `DataStore` protocol (feature 002) supports per-tenant data isolation. For cloud training, the `CloudTrainer` creates a tenant-scoped DataStore for each tenant:

```python
# PostgresStore supports tenant scoping
tenant_store = postgres_store.for_tenant(tenant_id)
tenant_model_store = s3_model_store.for_tenant(tenant_id)
trainer = Trainer(tenant_store, tenant_model_store)
result = trainer.train_all()
```

The `for_tenant()` pattern is defined by features 002/003. This feature codes against those interfaces.

### D4: CLI Extension

Extend the existing `train` subcommand with cloud mode options:

```
sigil-ml train                                    # Local mode (unchanged)
sigil-ml train --mode cloud --tenant <id>         # Single tenant
sigil-ml train --mode cloud --all-tenants         # Batch all tenants
sigil-ml train --mode cloud --aggregate           # Aggregate model
```

Cloud mode requires environment variables for Postgres and S3 configuration (same as features 002/003).

### D5: Concurrency Prevention

Use Postgres advisory locks keyed on a hash of the tenant ID. This prevents two CronJob instances from training the same tenant simultaneously.

```python
def acquire_tenant_lock(conn, tenant_id: str) -> bool:
    """Try to acquire an advisory lock. Returns False if already held."""
    lock_key = hash(tenant_id) & 0x7FFFFFFF  # 32-bit positive int
    row = conn.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,)).fetchone()
    return row[0]

def release_tenant_lock(conn, tenant_id: str) -> None:
    lock_key = hash(tenant_id) & 0x7FFFFFFF
    conn.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
```

Advisory locks are session-scoped: they auto-release if the process crashes.

### D6: Minimum Interval Tracking

Track the last training time per tenant. Two options:

**Option A**: Store last-trained timestamp in a `ml_training_runs` table in Postgres (Python-owned).
**Option B**: Check the ModelStore for the last-modified time of the tenant's model weights.

**Decision**: Option A. A `ml_training_runs` table gives richer audit data (sample count, duration, models trained, status). This table is Python-owned, consistent with the existing `ml_cursor`, `ml_predictions`, `ml_events` pattern.

```sql
CREATE TABLE IF NOT EXISTS ml_training_runs (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    status TEXT NOT NULL,           -- 'completed', 'failed', 'skipped'
    models_trained TEXT,            -- JSON array: ["stuck", "duration", ...]
    sample_count INTEGER,
    duration_ms INTEGER,
    error_message TEXT,
    started_at BIGINT NOT NULL,
    completed_at BIGINT
);
CREATE INDEX idx_ml_training_runs_tenant ON ml_training_runs(tenant_id, completed_at DESC);
```

### D7: Aggregate Training

Aggregate training pools data from opted-in tenants. Opt-in is determined by a tenant config flag (queried via DataStore). The aggregate model weights are saved under a special `__aggregate__` tenant prefix in ModelStore.

Sampling strategy for volume imbalance: cap each tenant's contribution at a configurable maximum (default: 1000 tasks) to prevent large tenants from dominating. If a tenant has fewer tasks, all their data is included.

### D8: Structured Output

Training output is structured JSON to stdout, one line per event (JSON Lines format for K8s log collection):

```json
{"event": "tenant_start", "tenant_id": "abc123", "ts": "2026-03-30T01:00:00Z"}
{"event": "tenant_complete", "tenant_id": "abc123", "status": "trained", "models": ["stuck", "duration", "activity", "workflow", "quality"], "samples": 150, "duration_ms": 2340}
{"event": "tenant_skip", "tenant_id": "def456", "reason": "recently_trained"}
{"event": "tenant_fail", "tenant_id": "ghi789", "error": "Insufficient data after filtering"}
{"event": "batch_complete", "total": 10, "trained": 8, "skipped": 1, "failed": 1, "duration_ms": 45000}
```

### D9: Configuration

Cloud training configuration via environment variables (consistent with features 002/003):

| Variable | Description | Default |
|----------|-------------|---------|
| `SIGIL_ML_MODE` | `local` or `cloud` | `local` |
| `SIGIL_ML_DB_URL` | Postgres connection URL (cloud mode) | required in cloud |
| `SIGIL_ML_S3_BUCKET` | S3 bucket for model weights | required in cloud |
| `SIGIL_ML_S3_REGION` | AWS region | `us-east-1` |
| `SIGIL_ML_S3_ENDPOINT` | S3-compatible endpoint (e.g., MinIO) | None (uses AWS) |
| `SIGIL_ML_TRAIN_MIN_INTERVAL` | Minimum seconds between retraining a tenant | `3600` |
| `SIGIL_ML_TRAIN_MIN_TASKS` | Minimum completed tasks for ML training (below: synthetic) | `10` |
| `SIGIL_ML_TRAIN_MAX_TASKS_PER_TENANT` | Cap for aggregate training sampling | `1000` |

CLI args override env vars for training-specific options:

```
--min-interval <seconds>
--min-tasks <count>
--max-tasks-per-tenant <count>   (aggregate only)
```

## Data Model

### Key Entities

#### TrainingRun

Represents a single execution of model training for one tenant or the aggregate pool.

```python
@dataclass
class TrainingRun:
    tenant_id: str
    status: str                    # "trained", "failed", "skipped"
    models_trained: list[str]      # e.g., ["stuck", "duration", "activity", "workflow", "quality"]
    sample_count: int
    duration_ms: int
    error: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
```

#### TrainingBatch

A collection of TrainingRuns from a batch execution.

```python
@dataclass
class TrainingBatch:
    runs: list[TrainingRun]
    total_duration_ms: int
    started_at: datetime
    completed_at: datetime

    @property
    def trained(self) -> int: ...
    @property
    def skipped(self) -> int: ...
    @property
    def failed(self) -> int: ...
```

#### CloudTrainingConfig

Configuration for cloud training runs.

```python
@dataclass
class CloudTrainingConfig:
    min_interval_sec: int = 3600
    min_tasks: int = 10
    max_tasks_per_tenant: int = 1000
    aggregate_min_tenants: int = 3    # Warn if fewer opted-in tenants
```

### Database Schema Addition

The `ml_training_runs` table (described in D6) is the only schema addition. It is created by the cloud training pipeline on first run, following the same pattern as `ml_cursor` (Python-owned).

## Dependency Graph

```
Feature 002 (DataStore protocol + SqliteStore + PostgresStore)
    |
    +---> Feature 003 (ModelStore protocol + LocalModelStore + S3ModelStore)
              |
              +---> Feature 004 (Cloud Training Pipeline) [THIS FEATURE]
                        |
                        +---> WP1: Refactor Trainer to use DataStore/ModelStore
                        |         (depends on 002 + 003 protocols being defined)
                        |
                        +---> WP2: CloudTrainer + tenant discovery
                        |         (depends on WP1)
                        |
                        +---> WP3: CLI extension + config
                        |         (depends on WP2)
                        |
                        +---> WP4: Locking + interval tracking
                        |         (depends on WP2)
                        |
                        +---> WP5: Aggregate training
                        |         (depends on WP2 + WP4)
                        |
                        +---> WP6: Observability + structured output
                              (depends on WP2)
```

### Sequential Implementation Note

Per the dependency strategy, features 002 and 003 are implemented first. This feature codes against their real `DataStore` and `ModelStore` protocols, which live in `src/sigil_ml/storage/` and `src/sigil_ml/model_storage/` respectively. The protocols should be importable when feature 004 development begins.

## Work Package Overview

| WP | Name | Priority | Depends On | Est. Complexity |
|----|------|----------|------------|-----------------|
| WP1 | Refactor Trainer for DataStore/ModelStore | P1 | Features 002, 003 | Medium |
| WP2 | CloudTrainer + Tenant Discovery | P1 | WP1 | Medium |
| WP3 | CLI Extension + Configuration | P1 | WP2 | Low |
| WP4 | Concurrency Locking + Interval Tracking | P1 | WP2 | Low |
| WP5 | Aggregate Training | P2 | WP2, WP4 | Medium |
| WP6 | Structured Logging + Observability | P3 | WP2 | Low |

### WP1: Refactor Trainer for DataStore/ModelStore

**Goal**: Make `Trainer` backend-agnostic so the same training logic works for both local SQLite and cloud Postgres.

**Changes**:
- `training/trainer.py`: Change constructor from `db_path` to `DataStore` + `ModelStore`. Replace direct SQLite queries with DataStore method calls. Replace direct `joblib.dump`/`joblib.load` with ModelStore calls.
- `features.py`: Refactor `extract_stuck_features()` and `extract_duration_features()` to accept a DataStore (or the data directly) instead of `db_path`.
- `training/scheduler.py`: Update to use DataStore for `_count_completed()` and `_log_retrain()`.
- `cli.py`: Update local `train` command to construct `SqliteStore` + `LocalModelStore` and pass to Trainer.
- `app.py`: Update startup to construct stores and pass to `TrainingScheduler`.
- Model classes (`stuck.py`, `duration.py`, etc.): Accept ModelStore in `train()` for saving weights. Keep `__init__` loading from config for backward compat in local mode (or accept optional ModelStore).

**Tests**: Verify Trainer works with a mock DataStore/ModelStore. Verify existing local training behavior is preserved.

### WP2: CloudTrainer + Tenant Discovery

**Goal**: Implement the cloud training orchestrator that iterates over tenants and delegates training.

**New files**:
- `training/cloud_trainer.py`: `CloudTrainer` class with `train_tenant()`, `train_all_tenants()`, `train_aggregate()` methods.
- `training/tenant_discovery.py`: `discover_eligible_tenants()` function that queries DataStore for distinct tenants with sufficient data and not recently trained.

**Key logic**:
- `train_tenant()`: Create tenant-scoped DataStore + ModelStore, construct Trainer, call `train_all()`, record TrainingRun.
- `train_all_tenants()`: Call `discover_eligible_tenants()`, iterate with fault isolation, produce TrainingBatch.
- Fault isolation: Each tenant wrapped in try/except. Failures logged, next tenant continues.

**Tests**: Mock DataStore returning multi-tenant data. Verify correct delegation. Verify fault isolation (one tenant fails, others succeed).

### WP3: CLI Extension + Configuration

**Goal**: Extend `sigil-ml train` with cloud mode options and wire up configuration.

**Changes**:
- `cli.py`: Add `--mode`, `--tenant`, `--all-tenants`, `--aggregate`, `--min-interval`, `--min-tasks`, `--max-tasks-per-tenant` arguments to the `train` subcommand.
- `config.py`: Add cloud training config functions that read from env vars with CLI arg overrides.

**CLI behavior**:
- `sigil-ml train` (no flags): Local mode, unchanged.
- `sigil-ml train --mode cloud --tenant X`: Single tenant training.
- `sigil-ml train --mode cloud --all-tenants`: Batch training.
- `sigil-ml train --mode cloud --aggregate`: Aggregate model training.
- Cloud mode validates that required env vars (`SIGIL_ML_DB_URL`, `SIGIL_ML_S3_BUCKET`) are set before proceeding.

**Tests**: Test CLI arg parsing. Test validation of required env vars in cloud mode.

### WP4: Concurrency Locking + Interval Tracking

**Goal**: Prevent concurrent training of the same tenant and track training intervals.

**New files**:
- `training/locking.py`: `TenantLock` context manager using Postgres advisory locks.

**Schema addition**:
- `ml_training_runs` table for tracking training history (created on first cloud training run).

**Key logic**:
- `TenantLock`: Context manager that acquires `pg_try_advisory_lock` on entry and releases on exit. If lock cannot be acquired, raises `TenantLockError`.
- `CloudTrainer.train_tenant()` wraps training in `TenantLock`.
- `discover_eligible_tenants()` checks `ml_training_runs` for the last completed run per tenant and filters by `min_interval_sec`.
- After each training run (success or failure), insert a row into `ml_training_runs`.

**Tests**: Test lock acquisition/release. Test interval filtering. Test behavior when lock is already held.

### WP5: Aggregate Training

**Goal**: Train aggregate models from pooled opted-in tenant data.

**Changes**:
- `training/cloud_trainer.py`: Implement `train_aggregate()`.
- `training/tenant_discovery.py`: Add `discover_opted_in_tenants()` that filters for tenants with a data-pooling opt-in flag.

**Key logic**:
- Query DataStore for all opted-in tenants.
- For each opted-in tenant, fetch completed tasks (capped at `max_tasks_per_tenant`).
- Pool all data into a single training dataset.
- Train using `Trainer` with `DataStore` wrapping the pooled data and `ModelStore` targeting the `__aggregate__` prefix.
- Warn if fewer than `aggregate_min_tenants` are opted in.

**Tests**: Mock multiple tenants with varying data volumes. Verify sampling cap. Verify pooling. Verify aggregate model saved to correct prefix.

### WP6: Structured Logging + Observability

**Goal**: Produce structured JSON output from training runs for K8s log collection and operator visibility.

**Changes**:
- `training/cloud_trainer.py`: Emit structured JSON log lines at each stage (tenant start, complete, skip, fail, batch summary).
- Configure Python `logging` to use JSON format when in cloud mode.

**Key logic**:
- Use `logging.getLogger("sigil_ml.training")` with a JSON formatter.
- Each log event is a JSON object with `event`, `tenant_id`, `ts`, and event-specific fields.
- Batch summary emitted at the end with counts and total duration.
- Training events also written to `ml_training_runs` table for persistence.

**Tests**: Capture log output and verify JSON structure. Verify summary counts match actual results.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Features 002/003 protocols change during development | Medium | Medium | Code against protocol interfaces, not implementations. Keep coupling loose. |
| Trainer refactor breaks existing local training | Medium | High | Run existing test suite after every refactor step. WP1 specifically validates no regressions. |
| Postgres advisory locks insufficient for distributed locking | Low | Low | Advisory locks are session-scoped and well-tested. K8s CronJob `concurrencyPolicy: Forbid` is the first line of defense. |
| Aggregate training memory usage with many tenants | Low | Medium | Cap per-tenant contribution. Process and discard per-tenant data incrementally if needed. |

## Open Questions (Resolved)

| Question | Resolution |
|----------|------------|
| Dependency strategy? | Sequential. Features 002/003 first, then 004 codes against real protocols. |
| CLI design? | `sigil-ml train --mode cloud --tenant X` / `--all-tenants` / `--aggregate` |
| Tenant discovery? | Query DataStore for distinct tenant IDs with sufficient data. |
| Locking? | Postgres advisory locks. Simple, no external deps. |
| Aggregate training opt-in? | Tenant config flag. Pool data across opted-in tenants. |
| Observability? | Structured JSON logging to stdout. K8s collects it. |
| Fault isolation? | Catch and log per-tenant failures, continue with next tenant. |
| Config? | Env vars for cloud (same as 002/003), CLI args for training-specific options. |
