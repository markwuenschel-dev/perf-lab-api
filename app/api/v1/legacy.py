"""
app/api/v1/legacy.py

Legacy v0.1 endpoints.
    POST /compute-metrics   RETIRED: always 410 Gone (see below)
    GET  /program/run
    GET  /program/strength
"""


from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["Legacy"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class RetiredEndpoint(BaseModel):
    detail: str
    replacement: str


class RunSession(BaseModel):
    week: int
    session: int
    day: str
    name: str
    focus: str
    zone: str
    rpe: str


class StrengthSession(BaseModel):
    week: int
    day: str
    phase: str
    main_lifts: str
    accessory: str
    notes: str


# ---------------------------------------------------------------------------
# Program data
# ---------------------------------------------------------------------------

RUN_SESSIONS: list[RunSession] = [
    RunSession(week=1, session=1, day="Mon", name="Intro Tempo Intervals", focus="3×4 min Tempo w/ 2 min easy jog", zone="Tempo", rpe="6–7"),
    RunSession(week=1, session=2, day="Thu", name="Pace Ladder 200–800–200", focus="200/400/600/800/600/400/200m @ RPE 6.5, easy jog between (total 4200m)", zone="Interval", rpe="6.5"),
    RunSession(week=2, session=1, day="Mon", name="Broken Tempo", focus="4×5 min Tempo w/ 90s easy jog", zone="Tempo", rpe="6–7"),
    RunSession(week=2, session=2, day="Thu", name="Short Intervals", focus="10×300m fast, 100m walk/jog between", zone="Fast Repeats", rpe="7–8"),
    RunSession(week=3, session=1, day="Mon", name="Tempo + Pickups", focus="12 min Tempo then 6×30s Fast Repeats / 60s easy", zone="Tempo → Fast", rpe="6–8"),
    RunSession(week=3, session=2, day="Thu", name="VO₂ Intro 400s", focus="8×400m Interval pace, 200m easy jog", zone="Interval", rpe="7–8"),
    RunSession(week=4, session=1, day="Mon", name="Threshold Waves", focus="3×(3 min Steady + 3 min Tempo), 3 min easy between sets", zone="Steady ↔ Tempo", rpe="6–7"),
    RunSession(week=4, session=2, day="Thu", name="600m Intervals", focus="5×600m Interval pace, 2:30 easy jog", zone="Interval", rpe="7–8"),
    RunSession(week=5, session=1, day="Mon", name="Progressive Tempo & Surges", focus="Steady blocks with repeated 45s RPE 8 surges (total 21:30 run)", zone="Steady ↔ Fast", rpe="3–4 base, 8 surges"),
    RunSession(week=5, session=2, day="Thu", name="VO₂ 800s", focus="5×800m Interval pace, 3:00 easy jog", zone="Interval", rpe="8"),
    RunSession(week=6, session=1, day="Mon", name="Tempo Block", focus="20 min continuous high Steady / low Tempo", zone="Steady / Tempo", rpe="6–7"),
    RunSession(week=6, session=2, day="Thu", name="Fast 300s", focus="12×300m Fast Repeats, 100m easy jog", zone="Fast Repeats", rpe="8"),
    RunSession(week=7, session=1, day="Mon", name="Light Tempo Intervals", focus="3×5 min Tempo, 2 min easy", zone="Tempo", rpe="6–7"),
    RunSession(week=7, session=2, day="Thu", name="Strides & Drills", focus="20–30 min Easy then 8×20s strides + drills", zone="Easy → Fast", rpe="3–4 easy, 7 strides"),
    RunSession(week=8, session=1, day="Mon", name="Race-Pace 600s", focus="6×600m Interval pace, 2:00 easy", zone="Interval", rpe="7–8"),
    RunSession(week=8, session=2, day="Thu", name="Alternating Tempo / Easy", focus="4×(3 min Tempo + 3 min Easy)", zone="Tempo / Easy", rpe="6–7"),
    RunSession(week=9, session=1, day="Mon", name="Taper Tempo", focus="2×8 min Tempo, 3 min easy", zone="Tempo", rpe="6–7"),
    RunSession(week=9, session=2, day="Thu", name="Sharpening 400s", focus="6×400m Fast Repeats, 200m easy jog", zone="Fast Repeats", rpe="7–8"),
    RunSession(week=10, session=1, day="Mon", name="Race Tune-Up", focus="6×200m Interval pace, 200m easy", zone="Interval", rpe="6–7"),
    RunSession(week=10, session=2, day="Sat/Sun", name="Race of Truth – 1.5 Mile Test", focus="Structured warm-up then 1.5 miles all-out, record time", zone="Race", rpe="9–10"),
]

STRENGTH_SESSIONS: list[StrengthSession] = []

for _wk in range(1, 4):
    STRENGTH_SESSIONS.append(StrengthSession(week=_wk, day="Tue", phase="Base", main_lifts="Back Squat 3×8; Walking Lunges 3×10/leg; Glute Bridge 3×12", accessory="Plank 3×:30; Side Plank 3×:20/side", notes="Controlled tempo; leave 2–3 reps in tank"))
    STRENGTH_SESSIONS.append(StrengthSession(week=_wk, day="Fri", phase="Base", main_lifts="DB Bench 3×10; Bent-Over Row 3×10; RDL 3×8", accessory="Hollow Hold 3×:20; Band Pull-Apart 3×15", notes="Avoid soreness that trashes run quality"))

for _wk in range(4, 8):
    STRENGTH_SESSIONS.append(StrengthSession(week=_wk, day="Tue", phase="Build", main_lifts="Back Squat 5×5 @ RPE 7–8; Reverse Lunges 3×8/leg; Hip Thrust 3×10", accessory="Hanging Knee Raise 3×10; Farmer Carry 3×40m", notes="Rest 2–3 min on heavy squats"))
    STRENGTH_SESSIONS.append(StrengthSession(week=_wk, day="Fri", phase="Build", main_lifts="Deadlift 5×3 @ RPE 7–8; Strict Press 4×6; Chin-ups 4×AMRAP", accessory="Back Extension 3×12; Weighted Plank 3×:30", notes="No grinders; crisp reps only"))

for _wk in range(8, 11):
    STRENGTH_SESSIONS.append(StrengthSession(week=_wk, day="Tue", phase="Power", main_lifts="Front Squat 4×3 @ RPE 6–7; Box Jump 4×3; KB Swing 4×10", accessory="Dead Bug 3×10/side; Single-leg Calf Raise 3×12/leg", notes="Explosive, low fatigue"))
    STRENGTH_SESSIONS.append(StrengthSession(week=_wk, day="Fri", phase="Recovery", main_lifts="SA DB Row 3×10/arm; Push-ups 3×AMRAP (2 in tank)", accessory="Side Plank 3×:30/side; Banded Monster Walk 3×12/leg", notes="Very submaximal; protect race legs"))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

COMPUTE_METRICS_RETIRED = RetiredEndpoint(
    detail=(
        "POST /compute-metrics is retired. Its VO2max estimate was miscalculated (a fixed 163 used "
        "as body mass in kg, sex fixed at male, a 2.76 time coefficient) and its grades and 300 m "
        "fatigue factor had no published basis. Record a 1.5-mile time trial instead; the "
        "corrected George et al. (1993) estimate is display-only."
    ),
    replacement=(
        "Record the run as a run_1p5mi_time_trial through POST /v1/onboard (which captures the "
        "body mass, sex and age the estimate needs), then read GET /v1/benchmarks/vo2-estimates"
    ),
)


# RETIRED 2026-10-21 (owner decision; docs/audits/2026-10-21-run-vo2-field-test-audit.md). A
# tombstone, not a deletion: the route stayed in the published contract, so an outside caller gets
# a clear 410 rather than a 404 or a wrong number. It deliberately declares no request body, so a
# valid, malformed or empty submission all receive the same retirement response.
@router.post(
    "/compute-metrics",
    status_code=410,
    response_model=RetiredEndpoint,
    deprecated=True,
    summary="Retired: always 410 Gone",
    responses={410: {"description": "This endpoint is retired; see `replacement`."}},
)
def compute_metrics() -> RetiredEndpoint:
    return COMPUTE_METRICS_RETIRED


@router.get("/program/run", response_model=list[RunSession])
def get_run_program() -> list[RunSession]:
    return RUN_SESSIONS


@router.get("/program/strength", response_model=list[StrengthSession])
def get_strength_program() -> list[StrengthSession]:
    return STRENGTH_SESSIONS
