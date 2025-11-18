import math
from typing import List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Performance Lab API")

origins = [
    "http://localhost:5173",               # Vite dev
    # later you'll add your deployed frontend URL, e.g.:
    # "https://perf-lab-web.netlify.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------- Helpers --------

def parse_time_to_seconds(text: str) -> float:
    text = text.strip()
    if not text:
        return 0.0
    parts = text.split(":")
    if len(parts) == 1:
        return float(parts[0])
    minutes = int(parts[0])
    seconds = float(parts[1])
    return minutes * 60 + seconds


def vo2_from_1p5(time_sec: float) -> float:
    return (483 * 60) / time_sec + 3.5


def vo2_category_male_36_45(vo2: float) -> str:
    if vo2 > 51:
        return "Superior"
    if vo2 > 42.9:
        return "Excellent"
    if vo2 > 38.9:
        return "Above Average"
    if vo2 > 34.9:
        return "Average"
    if vo2 > 30.9:
        return "Below Average"
    if vo2 > 25.9:
        return "Poor"
    return "Very Poor"


def result_category_male_30_39(time_sec: float) -> str:
    if time_sec <= 9 * 60 + 59:
        return "Superior"
    if time_sec <= 11 * 60:
        return "Excellent"
    if time_sec <= 12 * 60 + 30:
        return "Good"
    if time_sec <= 14 * 60 + 45:
        return "Fair"
    if time_sec <= 16 * 60 + 30:
        return "Poor"
    return "Very Poor"


def fatigue_factor(time_300: float, time_1p5: float) -> float:
    d1 = 300.0
    d2 = 2414.0  # 1.5 miles in meters (approx)
    return math.log(time_300 / time_1p5) / math.log(d1 / d2)


def fatigue_profile(ff_percent: float) -> str:
    if ff_percent < 18:
        return "Speed-biased (anaerobic strength)"
    if ff_percent <= 22:
        return "Balanced"
    return "Aerobic limitation (develop endurance)"


def pace_zone_bounds(base_pace_sec: float):
    defs = [
        ("Easy / Recovery", 150, 210, "Very easy; conversational. RPE 3–4"),
        ("Steady Aerobic", 90, 150, "Comfortable aerobic. RPE 4–5"),
        ("Tempo / Threshold", 40, 70, "Comfortably hard. RPE 6–7"),
        ("Interval (Race pace)", 0, 30, "1.5M race pace ± small. RPE 7–8"),
        ("Fast Repeats", -30, 0, "Faster than race pace. RPE 8–9"),
    ]
    zones = []
    for name, slow, fast, notes in defs:
        zones.append(
            {
                "name": name,
                "slow_offset_sec": slow,
                "fast_offset_sec": fast,
                "notes": notes,
                "slow_pace_sec": base_pace_sec + slow,
                "fast_pace_sec": base_pace_sec + fast,
            }
        )
    return zones


# -------- Models --------

class MetricsRequest(BaseModel):
    age: int
    sex: str
    time_300m: str
    time_1p5mi: str


class Zone(BaseModel):
    name: str
    slow_pace_sec: float
    fast_pace_sec: float
    slow_offset_sec: float
    fast_offset_sec: float
    notes: str


class MetricsResponse(BaseModel):
    vo2_max: float
    vo2_category: str
    result_category: str
    fatigue_percent: float
    fatigue_profile: str
    race_pace_sec_per_mile: float
    zones: List[Zone]


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


# -------- Program data (keyword args!) --------

RUN_SESSIONS: List[RunSession] = [
    RunSession(
        week=1,
        session=1,
        day="Mon",
        name="Intro Tempo Intervals",
        focus="3×4 min Tempo w/ 2 min easy jog",
        zone="Tempo",
        rpe="6–7",
    ),
    RunSession(
        week=1,
        session=2,
        day="Thu",
        name="Pace Ladder 200–800–200",
        focus="200/400/600/800/600/400/200m @ RPE 6.5, easy jog between (total 4200m)",
        zone="Interval",
        rpe="6.5",
    ),
    RunSession(
        week=2,
        session=1,
        day="Mon",
        name="Broken Tempo",
        focus="4×5 min Tempo w/ 90s easy jog",
        zone="Tempo",
        rpe="6–7",
    ),
    RunSession(
        week=2,
        session=2,
        day="Thu",
        name="Short Intervals",
        focus="10×300m fast, 100m walk/jog between",
        zone="Fast Repeats",
        rpe="7–8",
    ),
    RunSession(
        week=3,
        session=1,
        day="Mon",
        name="Tempo + Pickups",
        focus="12 min Tempo then 6×30s Fast Repeats / 60s easy",
        zone="Tempo → Fast",
        rpe="6–8",
    ),
    RunSession(
        week=3,
        session=2,
        day="Thu",
        name="VO₂ Intro 400s",
        focus="8×400m Interval pace, 200m easy jog",
        zone="Interval",
        rpe="7–8",
    ),
    RunSession(
        week=4,
        session=1,
        day="Mon",
        name="Threshold Waves",
        focus="3×(3 min Steady + 3 min Tempo), 3 min easy between sets",
        zone="Steady ↔ Tempo",
        rpe="6–7",
    ),
    RunSession(
        week=4,
        session=2,
        day="Thu",
        name="600m Intervals",
        focus="5×600m Interval pace, 2:30 easy jog",
        zone="Interval",
        rpe="7–8",
    ),
    RunSession(
        week=5,
        session=1,
        day="Mon",
        name="Progressive Tempo & Surges",
        focus="Steady blocks with repeated 45s RPE 8 surges (total 21:30 run)",
        zone="Steady ↔ Fast",
        rpe="3–4 base, 8 surges",
    ),
    RunSession(
        week=5,
        session=2,
        day="Thu",
        name="VO₂ 800s",
        focus="5×800m Interval pace, 3:00 easy jog",
        zone="Interval",
        rpe="8",
    ),
    RunSession(
        week=6,
        session=1,
        day="Mon",
        name="Tempo Block",
        focus="20 min continuous high Steady / low Tempo",
        zone="Steady / Tempo",
        rpe="6–7",
    ),
    RunSession(
        week=6,
        session=2,
        day="Thu",
        name="Fast 300s",
        focus="12×300m Fast Repeats, 100m easy jog",
        zone="Fast Repeats",
        rpe="8",
    ),
    RunSession(
        week=7,
        session=1,
        day="Mon",
        name="Light Tempo Intervals",
        focus="3×5 min Tempo, 2 min easy",
        zone="Tempo",
        rpe="6–7",
    ),
    RunSession(
        week=7,
        session=2,
        day="Thu",
        name="Strides & Drills",
        focus="20–30 min Easy then 8×20s strides + drills",
        zone="Easy → Fast",
        rpe="3–4 easy, 7 strides",
    ),
    RunSession(
        week=8,
        session=1,
        day="Mon",
        name="Race-Pace 600s",
        focus="6×600m Interval pace, 2:00 easy",
        zone="Interval",
        rpe="7–8",
    ),
    RunSession(
        week=8,
        session=2,
        day="Thu",
        name="Alternating Tempo / Easy",
        focus="4×(3 min Tempo + 3 min Easy)",
        zone="Tempo / Easy",
        rpe="6–7",
    ),
    RunSession(
        week=9,
        session=1,
        day="Mon",
        name="Taper Tempo",
        focus="2×8 min Tempo, 3 min easy",
        zone="Tempo",
        rpe="6–7",
    ),
    RunSession(
        week=9,
        session=2,
        day="Thu",
        name="Sharpening 400s",
        focus="6×400m Fast Repeats, 200m easy jog",
        zone="Fast Repeats",
        rpe="7–8",
    ),
    RunSession(
        week=10,
        session=1,
        day="Mon",
        name="Race Tune-Up",
        focus="6×200m Interval pace, 200m easy",
        zone="Interval",
        rpe="6–7",
    ),
    RunSession(
        week=10,
        session=2,
        day="Sat/Sun",
        name="Race of Truth – 1.5 Mile Test",
        focus="Structured warm-up then 1.5 miles all-out, record time",
        zone="Race",
        rpe="9–10",
    ),
]

STRENGTH_SESSIONS: List[StrengthSession] = []

for wk in range(1, 4):
    STRENGTH_SESSIONS.append(
        StrengthSession(
            week=wk,
            day="Tue",
            phase="Base",
            main_lifts="Back Squat 3×8; Walking Lunges 3×10/leg; Glute Bridge 3×12",
            accessory="Plank 3×:30; Side Plank 3×:20/side",
            notes="Controlled tempo; leave 2–3 reps in tank",
        )
    )
    STRENGTH_SESSIONS.append(
        StrengthSession(
            week=wk,
            day="Fri",
            phase="Base",
            main_lifts="DB Bench 3×10; Bent-Over Row 3×10; RDL 3×8",
            accessory="Hollow Hold 3×:20; Band Pull-Apart 3×15",
            notes="Avoid soreness that trashes run quality",
        )
    )

for wk in range(4, 8):
    STRENGTH_SESSIONS.append(
        StrengthSession(
            week=wk,
            day="Tue",
            phase="Build",
            main_lifts="Back Squat 5×5 @ RPE 7–8; Reverse Lunges 3×8/leg; Hip Thrust 3×10",
            accessory="Hanging Knee Raise 3×10; Farmer Carry 3×40m",
            notes="Rest 2–3 min on heavy squats",
        )
    )
    STRENGTH_SESSIONS.append(
        StrengthSession(
            week=wk,
            day="Fri",
            phase="Build",
            main_lifts="Deadlift 5×3 @ RPE 7–8; Strict Press 4×6; Chin-ups 4×AMRAP",
            accessory="Back Extension 3×12; Weighted Plank 3×:30",
            notes="No grinders; crisp reps only",
        )
    )

for wk in range(8, 11):
    STRENGTH_SESSIONS.append(
        StrengthSession(
            week=wk,
            day="Tue",
            phase="Power",
            main_lifts="Front Squat 4×3 @ RPE 6–7; Box Jump 4×3; KB Swing 4×10",
            accessory="Dead Bug 3×10/side; Single-leg Calf Raise 3×12/leg",
            notes="Explosive, low fatigue",
        )
    )
    STRENGTH_SESSIONS.append(
        StrengthSession(
            week=wk,
            day="Fri",
            phase="Recovery",
            main_lifts="SA DB Row 3×10/arm; Push-ups 3×AMRAP (2 in tank)",
            accessory="Side Plank 3×:30/side; Banded Monster Walk 3×12/leg",
            notes="Very submaximal; protect race legs",
        )
    )


# -------- Endpoints --------

@app.get("/ping")
def ping():
    return {"status": "ok"}


@app.post("/compute-metrics", response_model=MetricsResponse)
def compute_metrics(payload: MetricsRequest):
    t300 = parse_time_to_seconds(payload.time_300m)
    t15 = parse_time_to_seconds(payload.time_1p5mi)

    vo2 = vo2_from_1p5(t15)
    vo2_cat = vo2_category_male_36_45(vo2)  # TODO sex/age-specific later
    result_cat = result_category_male_30_39(t15)

    ff = fatigue_factor(t300, t15)
    ff_percent = (ff - 1.0) * 100.0
    profile = fatigue_profile(ff_percent)
    race_pace_sec = t15 / 1.5

    zone_dicts = pace_zone_bounds(race_pace_sec)
    zones = [Zone(**z) for z in zone_dicts]

    return MetricsResponse(
        vo2_max=vo2,
        vo2_category=vo2_cat,
        result_category=result_cat,
        fatigue_percent=ff_percent,
        fatigue_profile=profile,
        race_pace_sec_per_mile=race_pace_sec,
        zones=zones,
    )


@app.get("/program/run", response_model=List[RunSession])
def get_run_program():
    return RUN_SESSIONS


@app.get("/program/strength", response_model=List[StrengthSession])
def get_strength_program():
    return STRENGTH_SESSIONS
