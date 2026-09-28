"""Which wellness source to believe, per signal (provider-independent).

``WellnessSample`` is keyed ``(user_id, date, source)``, so several sources may report the
same day — an Oura sync and a manual check-in routinely do. Nothing consumed ``source``:
``_latest_wellness`` returned a single row ordered by ``created_at``, so whichever landed
last won and the other row's signals were dropped, and ``_baselines`` averaged every source
together so a provider switch silently shifted the athlete's own baseline.

This module owns the two judgements that fixes those, kept pure and free of any provider
name so a new integration needs no edit here.

**Objective vs subjective is the whole model.** A wearable measures HRV, resting heart rate
and sleep better than a person estimating them. But soreness, mood and stress are not
measurable by a device at all — a provider reporting them is inferring, while the athlete is
the primary instrument. So authority inverts by signal rather than being a single ranking of
providers.

**Two cloud devices are NOT ranked against each other.** There is no evidence in this repo
that one wearable measures HRV better than another, and inventing an order would be exactly
the kind of unbacked claim the readiness path is being cleaned of. Equal-authority sources
tie-break on ingestion recency, which is the previous behaviour narrowed to where it is
defensible.

**A phone-pushed source is a fallback, by decision rather than by accuracy (2026-09-28).**
Apple Watch data arrives through a phone automation (``POST /v1/wellness/ingest``). The
athlete decided that when a cloud-synced device (Oura) has a same-day reading, that reading
is used; otherwise the Apple Watch's is. That is a statement of which device the athlete
treats as primary, not a claim that one measures better, and a per-athlete source
preference may replace it later. A pushed source still outranks a hand-entered value on a
measured signal. Resolution is per day and per signal, so an Apple sleep reading still wins
on a day the ring reported only HRV.

Distinct from ``app.logic.observation_authority``, which governs whether an observation may
write *capacity* (and rejects device imports outright, ADR-0058). Wellness is not capacity;
conflating the two would import a rejection rule written for a different question.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

#: Signals only a human can report. A device that emits these is inferring, not measuring.
SUBJECTIVE_SIGNALS: frozenset[str] = frozenset({"soreness", "mood", "stress"})

#: Signals a wearable measures more reliably than a person estimating them.
OBJECTIVE_SIGNALS: frozenset[str] = frozenset(
    {"hrv_ms", "sleep_hours", "sleep_quality", "resting_hr"}
)

#: The athlete entering a value by hand. Every other source string is a provider/device.
MANUAL_SOURCE = "manual"

#: Sources a phone pushes through the ingest seam (``POST /v1/wellness/ingest``). They are
#: devices, but they rank below a cloud-synced device on the same day (see the module doc).
#: The ingest endpoint accepts exactly these source values.
PUSHED_DEVICE_SOURCES: frozenset[str] = frozenset({"apple_watch"})

#: Authority ranks. Only the ordering matters; the values are arbitrary.
_PRIMARY_DEVICE = 3
_PREFERRED = 2
_ACCEPTED = 1


def is_device_source(source: str) -> bool:
    """Anything that is not the athlete typing a value is a provider/device.

    Deliberately not a list of provider names: a new integration must not need an edit
    here, and an unrecognised provider must not silently fall to the bottom of a ranking.
    """
    return (source or "").strip().lower() != MANUAL_SOURCE


def authority_rank(signal: str, source: str) -> int:
    """How much this source's reading of this signal should be believed. Higher wins.

    Subjective signals invert the ordering rather than excluding devices: a provider-derived
    stress score is still evidence, just weaker than the athlete's own report.
    """
    device = is_device_source(source)
    if signal in SUBJECTIVE_SIGNALS:
        return _ACCEPTED if device else _PREFERRED
    if not device:
        return _ACCEPTED
    pushed = (source or "").strip().lower() in PUSHED_DEVICE_SOURCES
    return _PREFERRED if pushed else _PRIMARY_DEVICE


#: Ordering stand-in for a reading whose source reported no quality at all. Between a
#: reading the provider FLAGGED as degraded and one it did not, prefer the unflagged — that
#: is a comparison, not a claim. Nothing stores this value; `quality` stays NULL on the row,
#: because "no problem reported" is not the same as "measured perfectly".
_UNFLAGGED_ORDERING_QUALITY = 1.0


@dataclass(frozen=True)
class SignalCandidate:
    """One source's offer for one signal on one day."""

    source: str
    value: Any
    quality: float | None = None
    #: When the reading was taken, if the provider said. Preferred over ingestion time:
    #: between two devices, the later MEASUREMENT is the more current reading, whereas the
    #: later upload may just be a slower sync.
    measured_at: datetime | None = None
    #: When the row reached us. The fallback ordering signal, and all a legacy row has.
    ingested_at: datetime | None = None


def resolve_signal_source(
    signal: str, candidates: list[SignalCandidate]
) -> tuple[str, Any] | None:
    """Pick the source to believe for one signal from same-day candidates.

    Entries whose value is ``None`` are not candidates at all: a source that reported
    nothing for a signal must not outrank one that did simply by being more authoritative
    in general.

    Ordering, in strict precedence: authority for this signal, then provider-reported
    quality, then recency — measurement time where known, ingestion time otherwise.

    Returns ``(source, value)``, or ``None`` when no source supplied this signal — which
    stays missing rather than becoming a number.
    """
    supplied = [c for c in candidates if c.value is not None]
    if not supplied:
        return None

    def _key(c: SignalCandidate) -> tuple[int, float, bool, Any]:
        recency = c.measured_at or c.ingested_at
        return (
            authority_rank(signal, c.source),
            c.quality if c.quality is not None else _UNFLAGGED_ORDERING_QUALITY,
            # A row with no timestamp at all sorts last rather than raising on a None
            # comparison; the flag keeps datetimes from being compared against a filler.
            recency is not None,
            recency,
        )

    dated = [c for c in supplied if (c.measured_at or c.ingested_at) is not None]
    undated = [c for c in supplied if (c.measured_at or c.ingested_at) is None]
    best = max(dated, key=_key) if dated else max(undated, key=_key)
    if dated and undated:
        # Compare the winners on the first two components only; an undated row can still
        # win on authority or quality, it just cannot win a recency tie-break.
        best_undated = max(undated, key=_key)
        if _key(best_undated)[:2] > _key(best)[:2]:
            best = best_undated
    return best.source, best.value
