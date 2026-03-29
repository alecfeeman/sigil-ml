---
work_package_id: WP04
title: Concurrent Training Lock
lane: planned
dependencies: [WP02]
subtasks:
- T018
- T019
- T020
- T021
- T022
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
- FR-013
---

# Work Package Prompt: WP04 -- Concurrent Training Lock

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
spec-kitty implement WP04 --base WP02
```

Depends on WP02 (CloudTrainer.train_tenant exists to integrate with). Can proceed in parallel with WP03.

---

## Objectives & Success Criteria

- A `TrainingLock` protocol is defined with `acquire(tenant_id)` and `release(tenant_id)` methods
- A DataStore-backed implementation prevents concurrent training for the same tenant
- `CloudTrainer.train_tenant()` acquires the lock before training and releases in a `finally` block
- Stale locks (from crashed jobs) are detected and overridden after a configurable timeout
- Locked tenants appear as `"skipped_locked"` in the `TrainingRun` result

## Context & Constraints

- **Spec**: `kitty-specs/004-cloud-training-pipeline/spec.md` -- Edge Cases section and FR-013
- **Why distributed locking**: Multiple K8s pods could run training CronJobs concurrently. In-process locks (threading.Lock) are insufficient -- the lock must be at the data layer.
- **WP02 artifacts**: `CloudTrainer.train_tenant()` is the method to integrate with.
- **Simplicity**: Use the existing DataStore (database) for locking rather than introducing Redis or etcd. A simple table-based lock is sufficient for this use case.

---

## Subtasks & Detailed Guidance

### Subtask T018 -- Design TrainingLock protocol

- **Purpose**: Define a clean protocol for training locks that can be implemented against different backends.
- **Steps**:
  1. Create `src/sigil_ml/training/lock.py`:
     ```python
     """Training lock protocol and implementations for preventing concurrent training."""

     from __future__ import annotations

     import logging
     import time
     from typing import Protocol, runtime_checkable

     logger = logging.getLogger(__name__)

     # Default stale lock timeout: 2 hours
     STALE_LOCK_TIMEOUT_SEC = 7200


     @runtime_checkable
     class TrainingLock(Protocol):
         """Protocol for distributed training locks.

         Prevents concurrent training for the same tenant across
         multiple processes/pods.
         """

         def acquire(self, tenant_id: str) -> bool:
             """Attempt to acquire a training lock for the given tenant.

             Returns True if the lock was acquired, False if another
             process holds it (and it's not stale).
             """
             ...

         def release(self, tenant_id: str) -> None:
             """Release the training lock for the given tenant.

             No-op if the lock is not held.
             """
             ...
     ```
  2. The protocol is intentionally minimal -- just acquire/release. Stale lock detection is an implementation detail.
  3. The `acquire()` method returns a boolean (not raises an exception) so the caller can handle the "locked" case gracefully.
- **Files**: `src/sigil_ml/training/lock.py` (new)
- **Parallel?**: No -- T019 and T020 depend on this.
- **Validation**:
  - [ ] Protocol is importable and runtime-checkable
  - [ ] Method signatures are clear and documented

### Subtask T019 -- Implement DataStoreTrainingLock

- **Purpose**: Implement the lock using the DataStore (database) as the locking backend. This works across multiple processes and K8s pods.
- **Steps**:
  1. In `lock.py`, implement:
     ```python
     class DataStoreTrainingLock:
         """Training lock backed by a database table via DataStore.

         Uses an ml_training_locks table (or equivalent) to record
         lock state. The DataStore must support:
           - acquire_training_lock(tenant_id, pid, timeout_sec) -> bool
           - release_training_lock(tenant_id) -> None
         """

         def __init__(self, data_store: "DataStore", stale_timeout_sec: int = STALE_LOCK_TIMEOUT_SEC) -> None:
             self.data_store = data_store
             self.stale_timeout_sec = stale_timeout_sec
             self._pid = str(os.getpid())

         def acquire(self, tenant_id: str) -> bool:
             """Attempt to acquire the lock.

             Logic:
             1. Check if a lock exists for this tenant
             2. If no lock: INSERT and return True
             3. If lock exists and is stale (older than stale_timeout_sec): override it
             4. If lock exists and is fresh: return False
             """
             return self.data_store.acquire_training_lock(
                 tenant_id=tenant_id,
                 pid=self._pid,
                 timeout_sec=self.stale_timeout_sec,
             )

         def release(self, tenant_id: str) -> None:
             """Release the lock."""
             self.data_store.release_training_lock(tenant_id)
     ```
  2. **Alternative (if DataStore shouldn't have lock methods)**: Implement locking directly with SQL through a dedicated connection:
     ```python
     class DatabaseTrainingLock:
         def __init__(self, connection_url: str, stale_timeout_sec: int = STALE_LOCK_TIMEOUT_SEC) -> None:
             self.connection_url = connection_url
             self.stale_timeout_sec = stale_timeout_sec

         def acquire(self, tenant_id: str) -> bool:
             # Use INSERT ... ON CONFLICT for atomicity
             # Check timestamp for stale lock detection
             ...
     ```
  3. The database approach (INSERT ON CONFLICT) provides atomicity:
     - Postgres: `INSERT INTO ml_training_locks (tenant_id, acquired_at, pid) VALUES ($1, NOW(), $2) ON CONFLICT (tenant_id) DO UPDATE SET acquired_at = NOW(), pid = $2 WHERE ml_training_locks.acquired_at < NOW() - INTERVAL '$3 seconds'`
     - This atomically acquires the lock or detects that it's held
  4. Extend the DataStore Protocol stub (from WP01) with lock methods if using approach 1:
     ```python
     def acquire_training_lock(self, tenant_id: str, pid: str, timeout_sec: int) -> bool: ...
     def release_training_lock(self, tenant_id: str) -> None: ...
     ```
- **Files**: `src/sigil_ml/training/lock.py`
- **Parallel?**: No -- depends on T018.
- **Notes**: The lock table schema would be:
  ```sql
  CREATE TABLE IF NOT EXISTS ml_training_locks (
      tenant_id TEXT PRIMARY KEY,
      acquired_at BIGINT NOT NULL,  -- unix millis
      pid TEXT NOT NULL
  );
  ```
- **Validation**:
  - [ ] First acquire() returns True
  - [ ] Second acquire() for same tenant returns False (lock held)
  - [ ] release() followed by acquire() returns True
  - [ ] Stale lock (older than timeout) is overridden

### Subtask T020 -- Integrate lock into train_tenant()

- **Purpose**: Wrap `CloudTrainer.train_tenant()` with lock acquisition and release to prevent concurrent training for the same tenant.
- **Steps**:
  1. Add a `training_lock` parameter to `CloudTrainer.__init__()`:
     ```python
     class CloudTrainer:
         def __init__(
             self,
             data_store: DataStore,
             model_store: ModelStore,
             training_lock: TrainingLock | None = None,
         ) -> None:
             self.data_store = data_store
             self.model_store = model_store
             self.training_lock = training_lock
     ```
  2. In `train_tenant()`, acquire/release the lock:
     ```python
     def train_tenant(self, tenant_id: str) -> TrainingRun:
         # Acquire lock (if lock is configured)
         if self.training_lock is not None:
             if not self.training_lock.acquire(tenant_id):
                 logger.info("Skipping tenant %s: training lock held", tenant_id)
                 return TrainingRun(
                     tenant_id=tenant_id,
                     status="skipped_locked",
                 )

         try:
             # ... existing training logic ...
             return run
         finally:
             if self.training_lock is not None:
                 self.training_lock.release(tenant_id)
     ```
  3. The lock is optional (None) so that local development and testing can proceed without locking.
  4. The `finally` block ensures the lock is always released, even if training fails.
- **Files**: `src/sigil_ml/training/cloud_trainer.py`
- **Parallel?**: No -- modifies the core training path.
- **Validation**:
  - [ ] Training without lock configured works as before (lock=None)
  - [ ] Training with lock: lock acquired before data queries, released after
  - [ ] Training failure: lock is released in finally block
  - [ ] Lock not acquired: returns TrainingRun with status="skipped_locked"

### Subtask T021 -- Add lock-skip status to TrainingRun

- **Purpose**: Ensure the `TrainingRun` dataclass and batch summary correctly handle the "skipped_locked" status.
- **Steps**:
  1. Verify `TrainingRun.status` can hold `"skipped_locked"` (it uses a plain string, so this works already)
  2. Update `train_all_tenants()` to count locked skips:
     ```python
     if run.status == "skipped_locked":
         summary.skipped += 1  # Already counted as skip
     ```
  3. Document the full status vocabulary:
     - `"trained"` -- successfully trained with real data
     - `"trained_synthetic"` -- trained with synthetic data (cold start)
     - `"skipped_interval"` -- skipped due to minimum interval
     - `"skipped_threshold"` -- skipped due to insufficient data (alternative to synthetic)
     - `"skipped_locked"` -- skipped because another process holds the training lock
     - `"failed"` -- training failed with an error
  4. Update `TrainingSummary.to_dict()` to include a breakdown by status:
     ```python
     def to_dict(self) -> dict[str, Any]:
         status_counts = {}
         for run in self.runs:
             status_counts[run.status] = status_counts.get(run.status, 0) + 1
         return {
             # ... existing fields ...
             "status_breakdown": status_counts,
         }
     ```
- **Files**: `src/sigil_ml/training/models.py`
- **Parallel?**: Yes -- dataclass changes only.
- **Validation**:
  - [ ] `TrainingRun(status="skipped_locked")` serializes correctly
  - [ ] `TrainingSummary` status_breakdown shows locked count
  - [ ] All status strings are documented

### Subtask T022 -- Stale lock detection and override

- **Purpose**: Prevent deadlocks from crashed training jobs by detecting and overriding stale locks.
- **Steps**:
  1. In `DataStoreTrainingLock.acquire()`, implement stale detection:
     ```python
     def acquire(self, tenant_id: str) -> bool:
         # The DataStore lock method should handle stale detection:
         # 1. Try to INSERT new lock
         # 2. If conflict (lock exists):
         #    a. Check if lock.acquired_at + stale_timeout_sec < now
         #    b. If stale: UPDATE lock with new owner
         #    c. If fresh: return False
         return self.data_store.acquire_training_lock(
             tenant_id=tenant_id,
             pid=self._pid,
             timeout_sec=self.stale_timeout_sec,
         )
     ```
  2. Allow the stale timeout to be configurable via environment variable:
     ```python
     STALE_LOCK_TIMEOUT_SEC = int(os.environ.get("SIGIL_ML_LOCK_TIMEOUT_SEC", "7200"))
     ```
  3. When a stale lock is overridden, log a warning:
     ```python
     logger.warning(
         "Overriding stale training lock for tenant %s (held since %s, pid %s)",
         tenant_id, acquired_at, old_pid,
     )
     ```
  4. The logging is important for operational visibility -- stale locks indicate a previous job crashed.
- **Files**: `src/sigil_ml/training/lock.py`
- **Parallel?**: No -- extends the lock implementation.
- **Validation**:
  - [ ] Lock acquired 3 hours ago (stale) is overridden on next acquire()
  - [ ] Lock acquired 30 minutes ago (fresh) is NOT overridden
  - [ ] Stale override is logged as a warning
  - [ ] Custom timeout via env var is respected

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Race condition on lock acquire | Use database-level atomicity (INSERT ON CONFLICT) |
| Lock never released on crash | Stale lock timeout (default 2 hours) allows recovery |
| Lock adds latency to training | Lock operations are single-row DB queries (~1ms) |
| Over-aggressive stale timeout | Default 2 hours is conservative; configurable via env var |

---

## Review Guidance

- Key acceptance checkpoints:
  1. Lock protocol is clean and minimal
  2. Database-level atomicity prevents race conditions
  3. Lock is always released in a `finally` block
  4. Stale locks are detected and overridden with logging
  5. CloudTrainer works correctly with lock=None (backward compatible)
  6. "skipped_locked" status appears correctly in batch summaries
- Reviewers should mentally simulate: two CronJob pods start at the same time for the same tenant. Verify only one acquires the lock.

---

## Activity Log

- 2026-03-29T16:29:51Z -- system -- lane=planned -- Prompt created.
