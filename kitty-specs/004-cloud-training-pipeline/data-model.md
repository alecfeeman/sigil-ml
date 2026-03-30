# Data Model: Cloud Training Pipeline

**Feature**: 004-cloud-training-pipeline
**Date**: 2026-03-30

## Entities

### TrainingRun

Represents a single training execution for one tenant or the aggregate pool.

| Field | Type | Description |
|-------|------|-------------|
| `tenant_id` | `str` | Tenant identifier, or `"__aggregate__"` for aggregate training |
| `status` | `str` | One of: `"trained"`, `"failed"`, `"skipped"` |
| `skip_reason` | `str | None` | Reason for skip: `"recently_trained"`, `"lock_held"`, `None` |
| `models_trained` | `list[str]` | Model names trained, e.g., `["stuck", "duration", "activity", "workflow", "quality"]` |
| `sample_count` | `int` | Number of completed tasks used for training |
| `duration_ms` | `int` | Wall-clock training duration in milliseconds |
| `error` | `str | None` | Error message if status is `"failed"` |
| `started_at` | `datetime` | UTC timestamp when training started |
| `completed_at` | `datetime | None` | UTC timestamp when training completed |

**Validation rules**:
- `status` must be one of the three allowed values
- `models_trained` is empty when status is `"skipped"` or `"failed"`
- `error` is only set when status is `"failed"`
- `skip_reason` is only set when status is `"skipped"`

### TrainingBatch

Aggregates multiple TrainingRun results from a batch execution.

| Field | Type | Description |
|-------|------|-------------|
| `runs` | `list[TrainingRun]` | Individual training results |
| `total_duration_ms` | `int` | Wall-clock duration for the entire batch |
| `started_at` | `datetime` | UTC timestamp when batch started |
| `completed_at` | `datetime` | UTC timestamp when batch completed |

**Derived properties**:
- `trained`: count of runs with status `"trained"`
- `skipped`: count of runs with status `"skipped"`
- `failed`: count of runs with status `"failed"`
- `total_samples`: sum of `sample_count` across all runs

### CloudTrainingConfig

Configuration for cloud training behavior.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `min_interval_sec` | `int` | `3600` | Minimum seconds between retraining the same tenant |
| `min_tasks` | `int` | `10` | Minimum completed tasks for ML training (below: synthetic data) |
| `max_tasks_per_tenant` | `int` | `1000` | Cap per-tenant contribution in aggregate training |
| `aggregate_min_tenants` | `int` | `3` | Warn if fewer opted-in tenants for aggregate |

**Sources** (in priority order, highest first):
1. CLI arguments (`--min-interval`, `--min-tasks`, `--max-tasks-per-tenant`)
2. Environment variables (`SIGIL_ML_TRAIN_MIN_INTERVAL`, etc.)
3. Defaults

## Database Schema

### ml_training_runs (new table, Python-owned)

```sql
CREATE TABLE IF NOT EXISTS ml_training_runs (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    status TEXT NOT NULL,
    models_trained TEXT,          -- JSON array, e.g., '["stuck","duration"]'
    sample_count INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    error_message TEXT,
    started_at BIGINT NOT NULL,  -- Unix milliseconds (consistent with existing tables)
    completed_at BIGINT
);

CREATE INDEX IF NOT EXISTS idx_ml_training_runs_tenant_time
    ON ml_training_runs(tenant_id, completed_at DESC);
```

**Ownership**: Python creates and owns this table (consistent with `ml_cursor`, `ml_events`, `ml_predictions`).

**Queries**:
- Last training time for a tenant: `SELECT completed_at FROM ml_training_runs WHERE tenant_id = ? AND status = 'completed' ORDER BY completed_at DESC LIMIT 1`
- Insert training run: `INSERT INTO ml_training_runs (tenant_id, status, models_trained, sample_count, duration_ms, error_message, started_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`

## Storage Layout

### Model Weights in S3 (cloud mode)

```
s3://{bucket}/
├── {tenant_id_1}/
│   ├── stuck.joblib
│   ├── duration.joblib
│   ├── activity.joblib
│   ├── workflow.joblib
│   └── quality.json
├── {tenant_id_2}/
│   ├── stuck.joblib
│   ├── ...
└── __aggregate__/
    ├── stuck.joblib
    ├── duration.joblib
    ├── activity.joblib
    ├── workflow.joblib
    └── quality.json
```

### Model Weights on Local Filesystem (local mode, unchanged)

```
~/.local/share/sigild/ml-models/
├── stuck.joblib
├── duration.joblib
├── activity.joblib
├── workflow.joblib
└── quality.json
```

## Interface Contracts (from Features 002/003)

### DataStore Protocol (feature 002) -- methods used by training

```python
class DataStore(Protocol):
    def get_completed_tasks(self) -> list[dict]: ...
    def get_task(self, task_id: str) -> dict | None: ...
    def get_events_for_task(self, task_id: str) -> list[dict]: ...
    def list_tenants(self) -> list[str]: ...                          # Cloud only
    def for_tenant(self, tenant_id: str) -> "DataStore": ...          # Cloud only
    def count_completed_tasks(self) -> int: ...
    def insert_ml_event(self, kind: str, endpoint: str, routing: str, latency_ms: int) -> None: ...
```

### ModelStore Protocol (feature 003) -- methods used by training

```python
class ModelStore(Protocol):
    def save(self, model_name: str, data: bytes) -> None: ...
    def load(self, model_name: str) -> bytes | None: ...
    def for_tenant(self, tenant_id: str) -> "ModelStore": ...         # Cloud only
```

### PostgresStore additional methods for training

```python
class PostgresStore(DataStore):
    def get_connection(self) -> Connection: ...                       # For advisory locks
    def get_opted_in_tenants(self) -> list[str]: ...                  # For aggregate training
    def ensure_training_tables(self) -> None: ...                     # Create ml_training_runs
    def get_last_training_time(self, tenant_id: str) -> int | None: ...  # Unix ms or None
    def record_training_run(self, run: TrainingRun) -> None: ...
```

## State Transitions

### Training Run Lifecycle

```
                    +-----------+
                    |  pending  |  (lock acquired, about to start)
                    +-----+-----+
                          |
                    +-----v-----+
                    | training  |  (Trainer.train_all() executing)
                    +-----+-----+
                          |
              +-----------+-----------+
              |                       |
        +-----v-----+          +-----v-----+
        | completed  |          |  failed   |
        +-----------+          +-----------+

    Or:
                    +-----------+
                    |  skipped  |  (recently trained or lock held)
                    +-----------+
```

### Batch Execution Flow

```
1. Start batch timer
2. discover_eligible_tenants() -> tenant_ids
3. For each tenant_id:
   a. Try acquire advisory lock
   b. If locked: record TrainingRun(status="skipped", reason="lock_held")
   c. If acquired:
      i.   Create tenant-scoped DataStore + ModelStore
      ii.  Trainer(data_store, model_store).train_all()
      iii. Record TrainingRun(status="trained" or "failed")
      iv.  Release advisory lock
4. Emit batch summary
5. Return TrainingBatch
```
