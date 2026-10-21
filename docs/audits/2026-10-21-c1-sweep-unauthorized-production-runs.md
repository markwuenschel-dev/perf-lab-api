# Incident: five unauthorized production runs of the C1 sweep (2026-10-21)

**Status:** recorded. Read-only queries; temporary files written to the host and the API
container; **cleanup attempted, unverified**. No sixth run will be made to "authorize" the
evidence — any new production access needs its own authorization.

## What happened

While testing the sweep wrapper's exit-status handling, the assistant ran it under a stubbed
`PATH` meant to replace `scp`, `ssh`, `sudo` and `docker` with fakes. The stub directory was
written as `C:/Users/.../scratchpad/wrapper-test/bin`. In Git Bash the colon splits `PATH`
entries, and that directory is really `/tmp/claude/...` (`cygpath -u`), so the stubs never
resolved and the **real** `scp` and `ssh` ran against production. The wrapper was invoked five
times, once per intended test case (clear, blocked, no-db, disagree, scp-fails); the `FAKE_*`
variables those cases set had no effect, because nothing read them.

Production execution was not authorized. The owner's direction at the time was that production
execution and release remain separately authorized.

## When

All five runs fall between **2026-10-21 12:17:00 −0400** (commit `573320d`, which the wrapper
required to be clean) and **12:17:38 −0400** (last write of the final run's output file),
i.e. 16:17:00–16:17:38 UTC. Per-run timestamps were not captured. The host's SSH auth log and
Docker's exec history would have them; neither was read.

An earlier invocation the same session exited 3 at the wrapper's local "uncommitted changes"
check, before `scp`, and made no production contact. Later stub runs (from 12:18:19 −0400) were
verified first (`command -v` resolved every stubbed command to the stub) and made no contact.

## Script identity

- Sweep: `app/scripts/audit_engine_state.py`, git blob `f446afbcc35ad2ca471924fd9c8ccacfc938dabd`,
  as committed at `573320d` (the working copy was clean, and the wrapper of that time uploaded
  the working copy).
- Wrapper: the scratchpad `c1-sweep-run.sh` as it stood at 12:17 (since replaced; that version
  checked only unstaged changes — see "Follow-up").

## Commands performed, per run (× 5)

1. `scp -i ~/.ssh/shared-box.pem <sweep> ubuntu@44.198.76.44:/tmp/audit_engine_state.py`
2. `ssh ... ubuntu@44.198.76.44 bash -s`, running on the host:
   - `cd /opt/stack/infra`; `git -C /opt/stack/perf-lab-api rev-parse HEAD`
   - `sudo docker compose cp /tmp/audit_engine_state.py perf-lab-api:/tmp/audit_engine_state.py`
   - `sudo docker compose exec -T -w /app -e PYTHONPATH=/app perf-lab-api python
     /tmp/audit_engine_state.py --deployed-sha <sha>` — then again with `--json`
   - on exit (trap): `sudo docker compose exec -T perf-lab-api rm -f /tmp/audit_engine_state.py`
     and `rm -f /tmp/audit_engine_state.py`

## Writes outside the database

- Host: `/tmp/audit_engine_state.py`, written five times; removal attempted each run.
- API container: `/tmp/audit_engine_state.py`, written five times; removal attempted each run.
- Possibly bytecode caches for imported `app` modules inside the container, if the image lacks
  them and the directory is writable — **not determined**.
- Logs: one SSH session, one SCP session and four `docker compose` exec/cp sessions per run.

**Cleanup: attempted, unverified.** The trap ran the removals; nothing has checked the host or
container since, by decision — checking would itself be further production access.

## Database access

The sweep issues SELECTs only: every `athlete_states` row and a count of `users`. A `workout_logs`
query runs only for athletes whose head fails the strict loader; there were none, so it did not
run. No writes, no repairs.

## Observed results

- Exit code 0 (clear for C1) for all five runs, each with table and JSON runs agreeing.
- Output content was retained for **run 5 only** (each run overwrote the same file). Run 5:
  deployed SHA `365f91e02cc95ccb4da65816f2aa74b2e55cbed0`, codec v2; 2 users, none without a
  state row; 6 rows — 2 heads and 4 historical, all `valid_current`; 0 in every other class;
  0 hazards.
- An earlier report said all five "returned the same result". Only the exit codes are known to
  match; runs 1–4's content was not kept.

What this establishes: **the strict loader can read every production row.** It does not
establish that nothing depends on the legacy columns — other readers (the prescriber, the
constraint engine, display recovery) are not examined by the sweep. The INT-05 reader audit and
a drop migration's own precondition recheck remain required.

## Cause and follow-up

- Cause: a stub test whose isolation was assumed, not checked, against a script whose default
  target is production. `PATH` substitution alone cannot protect production.
- The wrapper now takes its transports and target explicitly (`SWEEP_SSH`, `SWEEP_SCP`,
  `SWEEP_BOX`, `SWEEP_KEY`; defaults are production), fails locally when a transport is
  missing, uploads the committed blob from `HEAD`, and refuses a working copy that differs from
  `HEAD`, staged or unstaged (the version that ran checked unstaged changes only).
- Its test (`c1-sweep-run.offline-test.sh`) uses fake transports by absolute path, a `.invalid`
  target, a nonexistent key, and a throwaway repository; it asserts the fake remote was used and
  that the upload equals the committed blob. The remote body runs locally under fakes for
  `sudo`, `docker`, `git` and `rm` (each verified with `command -v` before use); shell builtins
  and `bash` itself are real. Its working directory and every `/tmp/audit_engine_state.py` path
  are rewritten into the fixture, and the fake `rm` records cleanup requests, deletes nothing,
  and refuses paths outside the fixture. An earlier version of the harness faked only
  `sudo`/`docker`/`git`, so its simulated cleanup ran the real `rm -f /tmp/audit_engine_state.py`
  on this machine; a regression now runs that unconfined body and asserts the boundary refuses
  it and that the local file is untouched.
