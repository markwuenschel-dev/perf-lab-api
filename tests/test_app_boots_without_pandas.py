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

_BOOT = """
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
import app.main          # must not transitively import pandas
app.main.app.openapi()   # building the schema touches every router
print("BOOT_OK")
"""


def test_app_imports_and_builds_openapi_without_pandas():
    result = subprocess.run(
        [sys.executable, "-c", _BOOT], capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0 and "BOOT_OK" in result.stdout, (
        "app.main must import in the lean prod image (no pandas). A request-path module "
        "is eagerly importing the offline ML/analysis stack. Make that import lazy.\n\n"
        f"--- stderr tail ---\n{result.stderr[-3000:]}"
    )
