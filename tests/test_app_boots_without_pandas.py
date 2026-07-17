"""Guard: the app must import in the lean production image, where pandas (an
offline-only ML dependency) is NOT installed.

A runtime module that eagerly imports the ``app.ml`` / ``app.analysis`` stack drags
pandas into ``app.main``'s import graph; in the lean production image that made uvicorn
fail to load the app → the container stopped → the ``/ping`` healthcheck failed → every
deploy died (2026-07-07 onward). This test blocks pandas and imports the app in a
subprocess so it fails in CI the moment any request-path module re-introduces an eager
heavy import.
"""
import subprocess
import sys

# A subprocess program: block pandas via a meta-path finder, self-check that the block
# took (exit non-zero if pandas is still importable), run ``body``, then print BOOT_OK.
# ``body`` is spliced in at module level, so it runs with pandas already blocked.
_BOOT_TEMPLATE = """
import sys
class _BlockPandas:
    def find_spec(self, name, *args, **kwargs):
        if name == "pandas" or name.startswith("pandas."):
            raise ImportError("pandas blocked (simulating the lean production image)")
        return None
sys.meta_path.insert(0, _BlockPandas())
try:
    import pandas  # noqa: F401
    raise SystemExit("pandas was importable — the block did not take")
except ImportError:
    pass
{body}
print("BOOT_OK")
"""


def _run_boot(body: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, "-c", _BOOT_TEMPLATE.format(body=body)],
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_app_imports_and_builds_openapi_without_pandas():
    # Building the OpenAPI schema touches every router, so an eager heavy import on any
    # request path surfaces here.
    result = _run_boot("import app.main\napp.main.app.openapi()")
    assert result.returncode == 0 and "BOOT_OK" in result.stdout, (
        "app.main must import in the lean prod image (no pandas). A request-path module "
        "is eagerly importing the offline ML/analysis stack. Make that import lazy.\n\n"
        f"--- stderr tail ---\n{result.stderr[-3000:]}"
    )


def test_guard_is_red_capable_an_eager_pandas_import_fails_the_boot():
    """Prove the guard catches what it exists to catch.

    Splice an eager ``import pandas`` where the app import goes: it must fail the boot
    (non-zero exit, no BOOT_OK), exactly as a request-path module that pulled in the heavy
    stack would. Without this, the positive test above could pass vacuously if the block
    ever stopped taking effect — the harness would happily print BOOT_OK regardless.
    """
    result = _run_boot("import pandas  # a request-path module pulling in the heavy stack")
    assert result.returncode != 0 and "BOOT_OK" not in result.stdout, (
        "the pandas-block harness let an eager `import pandas` through — the guard would no "
        f"longer catch a regression.\n--- stdout ---\n{result.stdout}\n"
        f"--- stderr tail ---\n{result.stderr[-2000:]}"
    )
