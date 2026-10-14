"""Keep OAuth secrets out of the HTTP access log (ADR-0075).

Uvicorn's access log writes each request's path *with its query string*
(``uvicorn.protocols.utils.get_path_with_query_string``). An OAuth return carries the
authorization ``code`` and the ``state`` in that query, on the SPA callback path (served as the
app shell) and on the retired API callback, before any code of ours runs.

``RedactSensitiveQuery`` rewrites the logged target: the value of every sensitive parameter
becomes ``[redacted]``, whatever the case or percent-encoding of its name and however often it
repeats. It fails closed: a target it cannot process is logged without its query at all.
"""
from __future__ import annotations

import logging
from urllib.parse import unquote_plus

SENSITIVE_PARAMS = frozenset({
    "code", "state", "code_verifier", "error_description",
    "access_token", "refresh_token", "id_token", "token", "client_secret",
})
REDACTED = "[redacted]"
_SUPPRESSED = "?[query suppressed]"


def redact_target(target: str) -> str:
    """``target`` (path plus optional query) with every sensitive parameter value masked."""
    try:
        path, sep, query = target.partition("?")
        if not sep:
            return target
        parts: list[str] = []
        for segment in query.split("&"):
            raw_name, eq, _value = segment.partition("=")
            name = unquote_plus(raw_name, errors="strict").strip().lower()
            parts.append(f"{raw_name}={REDACTED}" if name in SENSITIVE_PARAMS else segment)
        return f"{path}?{'&'.join(parts)}"
    except Exception:  # noqa: BLE001 — never let an unparseable target through as it was
        return target.partition("?")[0] + _SUPPRESSED


class RedactSensitiveQuery(logging.Filter):
    """For ``uvicorn.access``: its record args are (client, method, target, http_version, status)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            args = record.args
            if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
                record.args = (*args[:2], redact_target(args[2]), *args[3:])
            elif "?" in record.getMessage():
                # An access record in a shape we don't know: keep the line, drop every query.
                record.msg = _strip_queries(record.getMessage())
                record.args = ()
        except Exception:  # noqa: BLE001 — fail closed: drop the record rather than leak it
            return False
        return True


def _strip_queries(message: str) -> str:
    return " ".join(
        word.partition("?")[0] + _SUPPRESSED if "?" in word else word for word in message.split(" ")
    )


def install() -> None:
    """Attach the filter to uvicorn's access logger (idempotent)."""
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactSensitiveQuery) for f in access.filters):
        access.addFilter(RedactSensitiveQuery())
