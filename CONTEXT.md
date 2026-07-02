# Contextual terminology for the athlete‑training domain

## AthleteContextRepository
A **repository** abstraction that provides the *data‑access boundary* for loading and persisting the athlete‑context records needed by services such as workout, prescription, planning, benchmark, and dashboard. It owns only persistence mechanics (fetching, inserting, linking, updating) and never contains domain logic like dose calculation, state updates, or benchmark mapping.

## Athlete context
The set of database‑backed records that together allow the system to make or update an athlete‑specific recommendation: profile, current state, recent workouts, active weak points, active block/session context, equipment, KPI snapshots, and benchmark observations. The repository is responsible for reading and writing these records.

## Repository (general definition)
A **repository** is a persistence boundary used by services to read/write ORM records. Repositories must not contain training‑engine decisions, dose calculations, or readiness logic; those belong in the service layer.

## AthleteContextRepository — interface contract
The seam is being built incrementally, one migrated query at a time; every method has real callers (no aspirational stubs).

- **Returns ORM rows**, never domain vectors. Callers convert rows to domain objects via `app.engine.state_bridge` (`unified_from_athlete_row`) in the service/engine layer. Returning domain vectors would pull that mapping into the repository — the domain‑logic leak this boundary exists to prevent.
- **Loading current state:** services and routes call `state_service.load_current_state` (read‑or‑`None`) or `state_service.load_or_init_current_state` (read‑or‑init) rather than re‑pairing `get_latest_state` with `unified_from_athlete_row` by hand. These loaders own that fetch→convert(→auto‑init) pairing in one place, above the repository seam — don't re‑scatter it back into callers. (The atomic staged‑baseline path inside `process_new_workout` stays bespoke: it stages the baseline for an atomic commit with the workout, so it can't use the committing loader.)
- **Transaction ownership stays with services** for now: services own `commit`; the repository owns query/read/write mechanics. A later slice may consolidate the commit boundary here.
- **Construction:** services build `AthleteContextRepository(session)` from the `AsyncSession` they already hold; routes may use a `get_athlete_repo` FastAPI dependency.
- **Tested** through the real interface against the `async_db` Postgres fixture — no in‑memory fake (a second adapter would be a hypothetical seam).
