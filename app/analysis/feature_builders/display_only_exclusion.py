"""Keep display-only observations out of every training frame (B2).

A display-only record (``observation_authority.DISPLAY_ONLY_PROTOCOL_CODES``, e.g. the 1.5 mi
time trial) has no authority anywhere, and its raw value is a time in seconds, not a score. A
frame that read it as an outcome would teach a future fit from it. Every generic builder that
reads ``benchmark_observations`` joins ``benchmark_definitions bd`` and filters with
:data:`DISPLAY_ONLY_FILTER`, bound by :func:`bind_display_only_codes`.
"""
from __future__ import annotations

from sqlalchemy import TextClause, bindparam

from app.logic.observation_authority import DISPLAY_ONLY_PROTOCOL_CODES

DISPLAY_ONLY_FILTER = "bd.code NOT IN :display_only_codes"


def bind_display_only_codes(query: TextClause) -> TextClause:
    return query.bindparams(
        bindparam("display_only_codes", value=sorted(DISPLAY_ONLY_PROTOCOL_CODES), expanding=True)
    )
