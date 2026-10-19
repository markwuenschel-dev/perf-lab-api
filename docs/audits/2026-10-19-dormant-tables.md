# Dormant tables: who reads them, and when each could be retired (2026-10-19)

The plan v3 audit (2026-10-03) called four tables dead. Searching every writer and reader on
`main` (`0dd53e2`) shows that two are fully dormant and two are read by an offline research
export. **Decision (wave B, B3): keep all four, and their export builders.** Dropping them would
need a destructive migration and production row counts, for little immediate benefit. This page
records what depends on each one and what would make it a retirement candidate.

No application code writes to any of them. Nothing in `app/api`, `app/schemas` or `web/src` names
them. Each has one foreign key, to `users.id`, with no `ondelete`. Nothing references them.

| Table | Model | Created | Production writer | Production reader | State |
|---|---|---|---|---|---|
| `pain_reports` | `PainReport`, `app/models/telemetry.py:136` | `a005_telemetry` | none | none | dormant |
| `outcome_events` | `OutcomeEvent`, `app/models/telemetry.py:151` | `a005_telemetry` | none | offline only: `app/analysis/feature_builders/tissue_risk_features.py` (`FROM outcome_events`) via `scripts/export_validation_datasets.py` (`Q3_tissue_risk`) | read by research export; no writer, so it exports 0 rows |
| `experiment_assignments` | `ExperimentAssignment`, `app/models/experiment.py:13` | `a006_experiment` | none | offline only: `app/analysis/feature_builders/experiment_features.py` (`FROM experiment_assignments`) via `scripts/export_validation_datasets.py` (`Q7_experiment`) | read by research export; only a test writes rows |
| `planning_overrides` | `PlanningOverride`, `app/models/planning_override.py:20` | `a021_planning_overrides` | none | none, on purpose: `app/logic/prescriber.py:1374` and `app/logic/planning_constraints.py:21` say P11 never reads it | reserved for P12 |

## What depends on each

- **`pain_reports`**: the in-memory model test in `tests/test_telemetry_models.py`. The tissue-risk hook
  `prior_pain_axes` (`app/logic/tissue_risk.py`) takes a set from its caller and never queries this
  table. ADR-0053 notes the hook is "fed by nothing".
- **`outcome_events`**: the Q3 tissue-risk export and its model card (`app/ml/q3_tissue/`), plus
  `tests/test_telemetry_models.py` and the import check in `tests/test_feature_builders.py`.
- **`experiment_assignments`**: the Q7 export, `tests/test_experiment_arms.py`, the import check in
  `tests/test_feature_builders.py`, and
  `tests/test_missed_sessions.py::test_offline_feature_builders_join_only_active_feedback`, which
  inserts rows into it against the migrated database.
- **`planning_overrides`**: [ADR-0051](../adr/0051-user-owns-structure-engine-owns-safety.md) (user
  overrides in the authority stack) and [ADR-0064](../adr/0064-receding-horizon-modality-assignment.md)
  (P12 compiles `PlanningOverride` rows into constraints). Both are `proposed`. Also
  `docs/REDESIGN_ROADMAP.md` (P12).

## When each becomes a retirement candidate

- **`pain_reports`**: if pain reporting gets designed, decide whether it uses this table or a new
  one. If it is dropped from the roadmap, this is the first candidate, since nothing reads it.
- **`outcome_events`** and **`experiment_assignments`**: when the Q3 and Q7 research questions are
  either given a real writer (an outcome-capture or experiment-assignment feature) or retired. If
  retired, the table, its feature builder, its `BUILDERS` entry and the tests above go together.
- **`planning_overrides`**: not a candidate while ADR-0051 and ADR-0064 plan P12 around it.

## If one is ever dropped

- **Before:** a read-only production row count.
- **The migration:** drop it in a migration of its own, whose `downgrade()` recreates the table
  exactly as `a005`/`a006`/`a021` defined it. Follow the guarded style of
  `a061_oauth_authorizations` and `a062_shadow_log_provenance`.
- **Shipping:** ship it schema-only, so its merge SHA becomes the rollback target (see
  `docs/DEPLOY.md`).
- **Tests:** remove the model and the table together; `tests/test_migration_model_drift.py` checks
  that they match.
