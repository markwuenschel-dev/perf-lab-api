"""
Export the FastAPI OpenAPI schema to a JSON file — the canonical API contract.

Run from the repo root:

    python -m app.scripts.export_openapi            # writes ./openapi.json
    python -m app.scripts.export_openapi --out p     # writes to a custom path
    python -m app.scripts.export_openapi --check      # CI drift gate (no write)

The committed ``openapi.json`` is the single source of truth the web app turns
into TypeScript types (``perf-lab-web``: ``npm run gen:types`` →
``src/types.gen.ts``). Regenerate and commit it whenever an API schema changes;
CI runs ``--check`` so the committed contract can never silently drift from the
code.

Output is pretty-printed with sorted keys so the file is deterministic — diffs
show real contract changes and ``--check`` is reproducible across machines.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Repo root = two levels up from app/scripts/, i.e. next to pyproject.toml.
DEFAULT_OUT = Path(__file__).resolve().parents[2] / "openapi.json"


def build_schema() -> dict[str, Any]:
    """Construct the OpenAPI document. Imported lazily so ``--help`` and arg
    parsing don't require the full app/runtime deps to be importable."""
    from app.main import app

    return app.openapi()


def render(schema: dict[str, Any]) -> str:
    """Serialize deterministically (sorted keys, trailing newline)."""
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export the FastAPI OpenAPI schema to a JSON file."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="Destination path (default: ./openapi.json at the repo root).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the file on disk matches the current schema; exit 1 on drift. "
        "Does not write. Intended for CI.",
    )
    args = parser.parse_args(argv)

    schema = build_schema()
    rendered = render(schema)
    n_paths = len(schema.get("paths", {}))
    n_schemas = len(schema.get("components", {}).get("schemas", {}))

    if args.check:
        if not args.out.exists():
            print(
                f"[export_openapi] {args.out} is missing — run "
                "`python -m app.scripts.export_openapi` and commit it.",
                file=sys.stderr,
            )
            return 1
        if args.out.read_text(encoding="utf-8") != rendered:
            print(
                f"[export_openapi] {args.out} is out of date — regenerate with "
                "`python -m app.scripts.export_openapi` and commit the result.",
                file=sys.stderr,
            )
            return 1
        print(f"[export_openapi] {args.out} is up to date ({n_paths} paths, {n_schemas} schemas).")
        return 0

    args.out.write_text(rendered, encoding="utf-8")
    print(f"[export_openapi] wrote {args.out} ({n_paths} paths, {n_schemas} schemas).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
