# Environment Setup

Every agent reads this file as part of its startup sequence. It documents project-specific constraints that affect how agents run commands and verify their work.

---

## Test Runner

**Stack:** Python / FastAPI / pytest-asyncio / asyncpg (PostgreSQL)

**Canonical test command:**
```bash
python -m pytest tests/ -v
```

**WSL / Windows venv constraint:**
This project's `.venv` uses Windows-native Python (`python.exe`). If you are running inside WSL, `python -m pytest` will fail with a path resolution error or silently skip. Use the Windows executable path directly:
```
.venv/Scripts/python.exe -m pytest tests/ -v
```
Or use the Windows-native terminal (PowerShell / cmd) to run tests.

**Test DB skip behaviour:**
All integration tests check for a live PostgreSQL connection at startup (see `tests/conftest.py`). If the DB is unreachable they skip gracefully — `SKIPPED` is not `FAILED`. A test that collects and skips is passing the CI contract.

**Verifying syntax without running tests:**
If the test runner is unavailable, use AST parsing as a fallback:
```bash
python -c "import ast; ast.parse(open('path/to/file.py').read()); print('OK')"
```
And verify test function names with:
```bash
python -c "
import ast, sys
tree = ast.parse(open(sys.argv[1]).read())
names = [n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name.startswith('test_')]
print(f'{len(names)} tests: {names}')
" tests/target_file.py
```

---

## Linter

**Tool:** ruff (configured in `pyproject.toml`)

**Run:**
```bash
ruff check app/
```

**Known pre-existing violations:** Several `B008`, `I001`, and `B904` warnings exist in the codebase before any new task begins. Do not treat these as regressions unless your task explicitly targets them.

---

## Import Verification

To confirm a module imports cleanly (no DeprecationWarnings, no missing deps):
```bash
python -W error::DeprecationWarning -c "import app.api.v1.module_name" 2>&1
```

---

## Database

- Engine: PostgreSQL (async via asyncpg + SQLAlchemy async)
- ORM: SQLAlchemy 2.x declarative with `async_sessionmaker`
- Migrations: Alembic (`alembic upgrade head` to apply)
- Local dev DB: configured via `DATABASE_URL` in `.env`

---

## Known Collection Errors (pre-existing, not your fault)

When running `pytest --collect-only` you may see collection errors in:
- Files referencing `constraint_engine` exports that are missing in the current install
- Files that require `numpy` (not installed in the system Python)

These are pre-existing. If your new file collects cleanly, the task is verified.
