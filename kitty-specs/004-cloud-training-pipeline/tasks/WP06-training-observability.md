---
work_package_id: WP06
title: Training Observability & Structured Output
lane: planned
dependencies:
- WP01
- WP03
- WP02
subtasks:
- T029
- T030
- T031
- T032
- T033
phase: Phase 3 - Polish
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
- FR-008
- FR-009
---

# Work Package Prompt: WP06 -- Training Observability & Structured Output

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
spec-kitty implement WP06 --base WP03
```

Depends on WP01 (TrainingRun/TrainingSummary dataclasses) and WP03 (batch training produces summary to format).

---

## Objectives & Success Criteria

- Training output is structured JSON, parseable by monitoring systems (FR-008)
- Per-tenant status includes: tenant_id, status, sample_count, models_trained, duration_sec, error_message
- Batch training produces a summary with trained/skipped/failed breakdowns
- Aggregate training produces a summary with pooled tenant count
- All training runs record audit events via DataStore (FR-009)
- CLI detects TTY vs pipe for formatting (pretty-print vs compact JSON)

## Context & Constraints

- **Spec**: `kitty-specs/004-cloud-training-pipeline/spec.md` -- User Story 5 (Training Observability), FR-008, FR-009
- **WP01 artifacts**: `TrainingRun` and `TrainingSummary` dataclasses with `to_dict()` and `to_json()` methods
- **WP03 artifacts**: `train_all_tenants()` returns a `TrainingSummary`
- **Existing pattern**: `TrainingScheduler._log_retrain()` records audit events to `ml_events` table
- **Output strategy**: Structured output (JSON) goes to stdout; log messages go to stderr. This allows piping JSON output while still seeing operational logs.

---

## Subtasks & Detailed Guidance

### Subtask T029 -- Enhance TrainingRun and TrainingSummary dataclasses

- **Purpose**: Ensure the dataclasses contain all fields needed for comprehensive observability.
- **Steps**:
  1. Review and enhance `TrainingRun` in `training/models.py`:
     ```python
     @dataclass
     class TrainingRun:
         tenant_id: str
         status: str  # trained, trained_synthetic, skipped_interval, skipped_threshold, skipped_locked, failed
         sample_count: int = 0
         models_trained: list[str] = field(default_factory=list)
         duration_sec: float = 0.0
         error_message: str | None = None
         # Observability additions:
         started_at: str | None = None  # ISO 8601 timestamp
         completed_at: str | None = None  # ISO 8601 timestamp
         data_freshness_sec: float | None = None  # seconds since newest training data
     ```
  2. Review and enhance `TrainingSummary`:
     ```python
     @dataclass
     class TrainingSummary:
         mode: str  # batch, aggregate, single
         total_tenants: int = 0
         trained: int = 0
         skipped: int = 0
         failed: int = 0
         total_duration_sec: float = 0.0
         runs: list[TrainingRun] = field(default_factory=list)
         # Observability additions:
         started_at: str | None = None  # ISO 8601
         completed_at: str | None = None  # ISO 8601
         status_breakdown: dict[str, int] = field(default_factory=dict)
     ```
  3. Update `to_dict()` methods to include the new fields
  4. Add a convenience method to compute status_breakdown from runs:
     ```python
     def compute_status_breakdown(self) -> None:
         self.status_breakdown = {}
         for run in self.runs:
             self.status_breakdown[run.status] = self.status_breakdown.get(run.status, 0) + 1
     ```
- **Files**: `src/sigil_ml/training/models.py`
- **Parallel?**: Yes -- dataclass changes only, can proceed alongside T030-T032.
- **Validation**:
  - [ ] All fields serialize to JSON correctly
  - [ ] status_breakdown accurately reflects run statuses
  - [ ] ISO 8601 timestamps are present in output

### Subtask T030 -- Structured JSON output for single-tenant training

- **Purpose**: When `--mode cloud --tenant <id>` is used, print structured JSON to stdout showing the training result.
- **Steps**:
  1. Define a Pydantic model for the output schema (for validation and documentation):
     ```python
     # In training/output.py or cli.py:
     from pydantic import BaseModel

     class TrainingRunOutput(BaseModel):
         tenant_id: str
         status: str
         sample_count: int
         models_trained: list[str]
         duration_sec: float
         error_message: str | None = None
         started_at: str | None = None
         completed_at: str | None = None
     ```
  2. In the CLI handler for `--tenant`:
     ```python
     if args.tenant:
         result = trainer.train_tenant(args.tenant)
         output = result.to_dict()

         if sys.stdout.isatty():
             # Pretty-print for terminal
             print(json.dumps(output, indent=2))
         else:
             # Compact JSON for pipes
             print(json.dumps(output))

         sys.exit(0 if result.status != "failed" else 1)
     ```
  3. If a `--json` flag is provided, always output compact JSON regardless of TTY
  4. Add `--json` flag to the train subcommand:
     ```python
     train_parser.add_argument("--json", action="store_true", help="Force JSON output")
     ```
- **Files**: `src/sigil_ml/cli.py`
- **Parallel?**: No -- modifies CLI output path.
- **Validation**:
  - [ ] Output is valid JSON
  - [ ] Contains all required fields: tenant_id, status, sample_count, models_trained, duration_sec
  - [ ] Pretty-printed when running in terminal
  - [ ] Compact when piped (`sigil-ml train ... | jq .`)
  - [ ] `--json` flag forces compact JSON

### Subtask T031 -- Structured JSON summary for batch training

- **Purpose**: When `--mode cloud --all-tenants` is used, print a structured JSON summary with per-tenant details.
- **Steps**:
  1. The batch training already returns a `TrainingSummary`. Enhance the CLI output:
     ```python
     elif args.all_tenants:
         summary = trainer.train_all_tenants()
         summary.compute_status_breakdown()
         output = summary.to_dict()

         if sys.stdout.isatty() and not args.json:
             # Pretty-print for terminal with human-readable header
             print(f"\n=== Training Summary ===")
             print(f"Mode: {summary.mode}")
             print(f"Tenants: {summary.total_tenants} total, "
                   f"{summary.trained} trained, "
                   f"{summary.skipped} skipped, "
                   f"{summary.failed} failed")
             print(f"Duration: {summary.total_duration_sec}s")
             print(f"\nDetailed JSON:")
             print(json.dumps(output, indent=2))
         else:
             print(json.dumps(output))

         sys.exit(0 if summary.failed == 0 else 1)
     ```
  2. The JSON output must include the full `runs` array with per-tenant details
  3. The human-readable header provides a quick overview when running interactively
- **Files**: `src/sigil_ml/cli.py`
- **Parallel?**: No -- modifies CLI output path.
- **Validation**:
  - [ ] JSON output includes `runs` array with all per-tenant results
  - [ ] `status_breakdown` shows counts per status type
  - [ ] Human-readable header in terminal mode
  - [ ] Compact JSON when piped
  - [ ] Per-tenant entries include all required fields (FR-008)

### Subtask T032 -- Structured JSON output for aggregate training

- **Purpose**: When `--mode cloud --aggregate` is used, print a structured JSON summary showing the aggregate training result.
- **Steps**:
  1. In the CLI handler for `--aggregate`:
     ```python
     elif args.aggregate:
         result = trainer.train_aggregate()
         summary = TrainingSummary(
             mode="aggregate",
             total_tenants=1,  # aggregate counts as one "tenant"
             trained=1 if result.status == "trained" else 0,
             skipped=1 if result.status.startswith("skipped") else 0,
             failed=1 if result.status == "failed" else 0,
             total_duration_sec=result.duration_sec,
             runs=[result],
         )
         output = summary.to_dict()

         if sys.stdout.isatty() and not args.json:
             print(f"\n=== Aggregate Training Summary ===")
             print(f"Status: {result.status}")
             print(f"Samples: {result.sample_count}")
             print(f"Models trained: {', '.join(result.models_trained)}")
             print(f"Duration: {result.duration_sec}s")
             if result.error_message:
                 print(f"Note: {result.error_message}")
             print(f"\nDetailed JSON:")
             print(json.dumps(output, indent=2))
         else:
             print(json.dumps(output))

         sys.exit(0 if result.status != "failed" else 1)
     ```
  2. The output should include the number of tenants whose data was pooled
- **Files**: `src/sigil_ml/cli.py`
- **Parallel?**: No -- modifies CLI output path.
- **Validation**:
  - [ ] JSON output includes sample_count and models_trained
  - [ ] Warning message appears if few tenants opted in
  - [ ] Human-readable header in terminal mode

### Subtask T033 -- Audit event recording for all training modes

- **Purpose**: Ensure all training runs (single-tenant, batch, aggregate) record structured audit events via DataStore (FR-009).
- **Steps**:
  1. Training events should already be recorded by `train_tenant()` (T012 in WP02). Verify this covers:
     - Single-tenant training (via `--tenant`)
     - Per-tenant training within a batch (via `--all-tenants`)
  2. Add audit event recording to `train_aggregate()` (already included in WP05 T026, verify it's consistent):
     ```python
     self.data_store.record_training_event(AGGREGATE_TENANT_ID, {
         "kind": "aggregate_training",
         "tenants_pooled": len(tenant_ids),
         "sample_count": total_samples,
         "models_trained": models_trained,
         "duration_ms": int(elapsed * 1000),
         "ts": int(time.time() * 1000),
     })
     ```
  3. Add a batch-level audit event for `train_all_tenants()`:
     ```python
     # At end of train_all_tenants():
     self.data_store.record_training_event("__batch__", {
         "kind": "batch_training",
         "total_tenants": summary.total_tenants,
         "trained": summary.trained,
         "skipped": summary.skipped,
         "failed": summary.failed,
         "duration_ms": int(summary.total_duration_sec * 1000),
         "ts": int(time.time() * 1000),
     })
     ```
  4. Audit events should use the existing `ml_events` table pattern:
     - `kind`: "training" | "batch_training" | "aggregate_training"
     - `endpoint`: "cloud_trainer"
     - `routing`: tenant_id or "__batch__" or "__aggregate__"
     - `latency_ms`: duration
     - `ts`: unix timestamp in milliseconds
  5. Audit events should be recorded EVEN on failure (with status in the event data)
- **Files**: `src/sigil_ml/training/cloud_trainer.py`
- **Parallel?**: Yes -- independent from output formatting.
- **Validation**:
  - [ ] Single-tenant training records an audit event
  - [ ] Batch training records per-tenant events AND a batch-level event
  - [ ] Aggregate training records an aggregate-level event
  - [ ] Failed training still records an audit event with failure status
  - [ ] Event schema matches `ml_events` table structure

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Output schema breaking monitoring | Define Pydantic models for validation; version the schema |
| Mixing structured and log output | stdout for structured data, stderr for logs |
| Large batch summaries | `--json` flag for compact output; summary header provides quick overview |
| Audit event write failures | Catch and log, don't fail the training run |

---

## Review Guidance

- Key acceptance checkpoints:
  1. All output is valid JSON (parseable by `jq`)
  2. TTY detection works correctly (pretty vs compact)
  3. `--json` flag forces compact JSON
  4. Per-tenant details include all FR-008 fields: tenant_id, status, sample_count, models_trained, duration
  5. Audit events recorded for all training modes
  6. Failed training produces both output AND audit events
  7. Exit codes follow the documented strategy (0=success, 1=failure)
- Reviewers should run `sigil-ml train --mode cloud --all-tenants | jq .` and verify the output is valid JSON with the expected structure.

---

## Activity Log

- 2026-03-29T16:29:51Z -- system -- lane=planned -- Prompt created.
