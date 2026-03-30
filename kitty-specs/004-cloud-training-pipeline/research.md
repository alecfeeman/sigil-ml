# Research: Cloud Training Pipeline

**Feature**: 004-cloud-training-pipeline
**Date**: 2026-03-30
**Status**: Complete

## R1: Postgres Advisory Locks for Training Concurrency

**Decision**: Use `pg_try_advisory_lock(key)` with session-scoped locks keyed on tenant ID hash.

**Rationale**:
- Advisory locks are built into Postgres -- no external dependencies (Redis, ZooKeeper, etc.)
- Session-scoped: automatically released if the process crashes or the connection drops
- `pg_try_advisory_lock` is non-blocking: returns `false` immediately if lock is held, so the CronJob can skip that tenant and move on
- The lock key is a 64-bit integer. We derive it from `hash(tenant_id) & 0x7FFFFFFFFFFFFFFF` to ensure positive values
- K8s CronJob `concurrencyPolicy: Forbid` prevents overlapping CronJob pods entirely, but advisory locks provide defense-in-depth for race conditions during overlapping scaling events

**Alternatives considered**:
- Redis distributed locks (rejected: adds external dependency)
- File-based locking (rejected: doesn't work across pods)
- `SELECT ... FOR UPDATE SKIP LOCKED` (rejected: requires a dedicated row per tenant, more schema overhead)

## R2: Trainer Refactoring to DataStore/ModelStore

**Decision**: Modify `Trainer.__init__` signature from `db_path: str | Path` to `data_store: DataStore, model_store: ModelStore`. Refactor `features.py` extraction functions to accept DataStore or pre-fetched data.

**Rationale**:
- The current Trainer opens SQLite connections in 4 places: `_train_stuck()`, `_train_duration()`, and indirectly via `features.py` functions
- The DataStore protocol (feature 002) provides `get_completed_tasks()`, `get_task(task_id)`, `get_events_for_task(task_id)` methods that replace all direct SQLite access
- The ModelStore protocol (feature 003) provides `save(model_name, data)` that replaces `joblib.dump()` calls in model classes
- Feature extraction functions in `features.py` currently take `db_path` and query SQLite directly. Two refactoring options:
  - **Option A**: Pass DataStore to feature functions (mirrors current db_path parameter)
  - **Option B**: Pre-fetch data in Trainer and pass raw dicts to feature functions
  - **Chosen**: Option A for consistency. Feature functions take DataStore, which is the same abstraction level as the current db_path parameter.

**Alternatives considered**:
- Keeping a separate `CloudTrainer` that duplicates training logic (rejected: violates DRY, spec FR-004 requires reuse)
- Making Trainer a Protocol with local/cloud implementations (rejected: overengineering, the algorithms are identical)

## R3: Tenant Discovery Strategy

**Decision**: Query the DataStore for distinct tenant IDs, filter by data sufficiency and training recency.

**Rationale**:
- The PostgresStore (feature 002) supports per-tenant data isolation. A method like `list_tenants()` returns all tenant IDs with data.
- For each tenant, check: (a) count of completed tasks >= `min_tasks` threshold for ML training (below this, synthetic data is used -- but training still runs), (b) last training run timestamp from `ml_training_runs` table respects `min_interval_sec`.
- Note: even tenants below `min_tasks` are eligible for training -- they use synthetic data (matching local cold-start behavior per spec acceptance scenario 1.2). The threshold only determines whether real or synthetic data is used.
- The "all tenants" batch discovers and iterates. The "single tenant" mode skips discovery.

**Alternatives considered**:
- External tenant registry service (rejected: unnecessary external dependency)
- Tenant list in config file (rejected: doesn't scale, requires manual updates)

## R4: Aggregate Training Sampling

**Decision**: Cap each tenant's contribution at `max_tasks_per_tenant` (default 1000) to prevent data imbalance.

**Rationale**:
- Without capping, a tenant with 10,000 tasks would dominate the aggregate model over tenants with 50 tasks
- Random sampling within each tenant's data ensures representative distribution
- The cap is configurable via `SIGIL_ML_TRAIN_MAX_TASKS_PER_TENANT` env var or `--max-tasks-per-tenant` CLI arg
- After pooling, the aggregate training uses the same `Trainer.train_all()` method
- Aggregate model weights are saved under the `__aggregate__` prefix in ModelStore

**Alternatives considered**:
- Weighted loss functions per tenant (rejected: added complexity, requires model changes)
- Equal-size sampling from each tenant (rejected: wastes data from data-rich tenants)

## R5: Structured JSON Logging

**Decision**: Use Python `logging` with a JSON formatter to stdout. One JSON object per log line (JSON Lines format).

**Rationale**:
- K8s collects stdout/stderr from containers. JSON Lines is the standard format for structured log ingestion.
- No additional dependencies needed -- Python's `logging` module with a custom `Formatter` subclass
- Each log event has a consistent schema: `{"event": "...", "tenant_id": "...", "ts": "...", ...}`
- The JSON formatter is only activated in cloud mode. Local mode keeps the existing plain-text logging.
- The training summary is also emitted as a log line for easy grep/filter

**Alternatives considered**:
- `structlog` library (rejected: new dependency, constitution says minimal deps)
- Print statements with JSON dumps (rejected: bypasses logging framework, harder to control levels)

## R6: ml_training_runs Table Design

**Decision**: Create a `ml_training_runs` table in Postgres owned by the Python process, following the pattern of `ml_cursor` and `ml_events`.

**Rationale**:
- Needed for: (a) tracking last training time per tenant for interval enforcement, (b) audit trail for training runs, (c) structured summary data for operators
- The table is created by the cloud training pipeline on first run (idempotent `CREATE TABLE IF NOT EXISTS`)
- Fields: id, tenant_id, status, models_trained (JSON), sample_count, duration_ms, error_message, started_at, completed_at
- Index on (tenant_id, completed_at DESC) for efficient "last training time" queries
- Consistent with existing Python-owned tables: `ml_predictions`, `ml_events`, `ml_cursor`

**Alternatives considered**:
- Store training metadata in S3 alongside model weights (rejected: harder to query, no SQL index for interval checks)
- Store in a separate training metadata table per tenant (rejected: single table is simpler, tenant_id column provides isolation)

## R7: Feature Extraction Refactoring Scope

**Decision**: Refactor `extract_stuck_features()` and `extract_duration_features()` in `features.py` to accept DataStore. The `extract_features_from_buffer()` and `extract_workflow_features()` functions remain unchanged (they operate on in-memory event lists, not database queries).

**Rationale**:
- `extract_stuck_features(db_path, task_id)` internally calls `_query_task()` and `_query_events_for_task()` -- both do direct SQLite queries
- `extract_duration_features(db_path, task_id)` has the same pattern
- These two functions need DataStore to become backend-agnostic
- `extract_features_from_buffer(events)` takes an in-memory list -- no database access, no change needed
- `extract_workflow_features(classified_events, session_info)` is pure computation -- no change needed
- The helper functions `_query_task()` and `_query_events_for_task()` are replaced by DataStore methods

**Impact**: 2 public functions change signature (`db_path` -> `data_store`), 2 private helpers are removed (replaced by DataStore methods), 2 public functions unchanged.
