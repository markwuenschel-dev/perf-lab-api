"""Deprecated alternate entrypoint. It forwards to the canonical app, ``app.main:app``.

``uvicorn main:app`` used to start a separate, stale app: six routers, ``/dashboard`` and
``/benchmarks`` without the ``/v1`` prefix, and CORS open to every origin with credentials.
Nothing in this repo, the Docker image or the deploy scripts starts it. Anyone who still runs it
now gets the real app, with every route and the production security settings.
"""

import warnings

warnings.warn(
    "The root 'main.py' (uvicorn main:app) is DEPRECATED and now forwards to app.main:app. "
    "Run uvicorn app.main:app instead. This file will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)

from app.main import app  # noqa: E402, F401  (re-exported for `uvicorn main:app`)
